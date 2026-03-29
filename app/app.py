from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_session import Session                  # ← AJOUTÉ
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from functools import wraps
import requests
import bcrypt                                      # ← AJOUTÉ
import os
import time
from datetime import timedelta

app = Flask(__name__)
CORS(app, supports_credentials=True)              # supports_credentials pour les cookies

# ─────────────────────────────────────────────────────────────
#  CONFIG SESSION (flask-session stocke côté serveur)
# ─────────────────────────────────────────────────────────────
# "filesystem" = les sessions sont des fichiers dans /tmp/flask_sessions
# → le cookie du navigateur contient juste un ID, pas les données
app.secret_key = os.getenv('SECRET_KEY', 'cloudbot-ginf2-ensa-tanger-2026')
app.config['SESSION_TYPE']               = 'filesystem'
app.config['SESSION_FILE_DIR']           = '/tmp/flask_sessions'
app.config['SESSION_PERMANENT']          = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
app.config['SESSION_COOKIE_HTTPONLY']    = True    # JS ne peut pas lire le cookie
app.config['SESSION_COOKIE_SAMESITE']   = 'Lax'   # protection CSRF basique
# app.config['SESSION_COOKIE_SECURE']   = True     # activer si HTTPS

Session(app)

# ─────────────────────────────────────────────────────────────
#  CONFIG MYSQL (identique à ton code)
# ─────────────────────────────────────────────────────────────
MYSQL_USER     = os.getenv('MYSQL_USER', 'chatbot')
MYSQL_PASSWORD = os.getenv('MYSQL_PASSWORD', 'chatbot123')
MYSQL_HOST     = os.getenv('MYSQL_HOST', 'db')
MYSQL_PORT     = os.getenv('MYSQL_PORT', '3306')
MYSQL_DB       = os.getenv('MYSQL_DB', 'chatbot')

app.config['SQLALCHEMY_DATABASE_URI'] = (
    f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"
    "?charset=utf8mb4"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_recycle': 280,
    'pool_pre_ping': True,
}

OLLAMA_URL   = os.getenv('OLLAMA_URL', 'http://ollama:11434')
OLLAMA_MODEL = os.getenv('OLLAMA_MODEL', 'tinyllama')

db = SQLAlchemy(app)

# ─────────────────────────────────────────────────────────────
#  PROMETHEUS METRICS (identique + login ajouté)
# ─────────────────────────────────────────────────────────────
REQUEST_COUNT   = Counter('chatbot_requests_total', 'Total chat requests', ['status'])
REQUEST_LATENCY = Histogram('chatbot_request_duration_seconds', 'Request latency')
TOKEN_COUNT     = Counter('chatbot_tokens_total', 'Total tokens generated')
LOGIN_COUNT     = Counter('chatbot_login_total', 'Login attempts', ['status'])  # ← AJOUTÉ

# ─────────────────────────────────────────────────────────────
#  MODÈLES SQLAlchemy
# ─────────────────────────────────────────────────────────────

class User(db.Model):
    """
    Table users — créée automatiquement par db.create_all()
    Le mot de passe n'est JAMAIS stocké en clair.
    Seulement le hash bcrypt (60 caractères).
    """
    __tablename__ = 'users'

    id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username      = db.Column(db.String(80),  nullable=False)
    email         = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at    = db.Column(db.DateTime, server_default=db.func.now())
    last_login    = db.Column(db.DateTime, nullable=True)

    # Un user a plusieurs conversations
    conversations = db.relationship('Conversation', backref='user',
                                    lazy=True, cascade='all, delete-orphan')

    def set_password(self, password: str):
        """Hash + stocke le mot de passe avec bcrypt."""
        self.password_hash = bcrypt.hashpw(
            password.encode('utf-8'),
            bcrypt.gensalt()
        ).decode('utf-8')

    def check_password(self, password: str) -> bool:
        """Vérifie si le mot de passe correspond au hash stocké."""
        return bcrypt.checkpw(
            password.encode('utf-8'),
            self.password_hash.encode('utf-8')
        )


class Conversation(db.Model):
    """Table conversations — user_id ajouté pour lier à l'utilisateur."""
    __tablename__ = 'conversations'

    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    session_id = db.Column(db.String(64), nullable=False, index=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    messages   = db.relationship('Message', backref='conversation',
                                 lazy=True, cascade='all, delete-orphan')


class Message(db.Model):
    """Table messages — identique, rien ne change."""
    __tablename__ = 'messages'

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id'), nullable=False)
    role            = db.Column(db.String(16), nullable=False)
    content         = db.Column(db.Text(65535), nullable=False)
    created_at      = db.Column(db.DateTime, server_default=db.func.now())


# ─────────────────────────────────────────────────────────────
#  DÉCORATEUR login_required
# ─────────────────────────────────────────────────────────────
def login_required(f):
    """
    Protège une route.

    Comment ça marche :
      1. Le navigateur envoie le cookie de session à chaque requête
      2. Flask lit le cookie → trouve le fichier session → lit user_id
      3. Si user_id présent → laisse passer
      4. Sinon → redirige vers /login (ou retourne 401 pour les requêtes AJAX)

    Utilisation :
        @app.route('/chat')
        @login_required
        def chat_page():
            ...
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': 'Non authentifié', 'redirect': '/login'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


# ─────────────────────────────────────────────────────────────
#  ROUTES PAGES HTML
# ─────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/login')
def login_page():
    if 'user_id' in session:
        return redirect(url_for('chat_page'))
    return render_template('login.html')

@app.route('/chat')
@login_required
def chat_page():
    return render_template('chat.html', username=session.get('username'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login_page'))


# ─────────────────────────────────────────────────────────────
#  API AUTH — INSCRIPTION  POST /api/auth/register
# ─────────────────────────────────────────────────────────────
@app.route('/api/auth/register', methods=['POST'])
def register():
    """
    Flux :
      1. Reçoit { username, email, password }
      2. Valide les champs
      3. Vérifie que l'email n'existe pas déjà
      4. Hash le mot de passe avec bcrypt
      5. Insère en base + crée la session (auto-login)
      6. Retourne 201

    Codes :
      201 → succès
      400 → champ manquant / mdp trop court / email invalide
      409 → email déjà utilisé
    """
    data = request.get_json()

    username = (data.get('username') or '').strip()
    email    = (data.get('email')    or '').strip().lower()
    password = (data.get('password') or '').strip()

    if not username or not email or not password:
        return jsonify({'error': 'Tous les champs sont requis'}), 400
    if len(password) < 8:
        return jsonify({'error': 'Mot de passe trop court (8 caractères minimum)'}), 400
    if '@' not in email:
        return jsonify({'error': 'Email invalide'}), 400

    if User.query.filter_by(email=email).first():
        return jsonify({'error': 'Cet email est déjà utilisé'}), 409

    user = User(username=username, email=email)
    user.set_password(password)          # bcrypt ici
    db.session.add(user)
    db.session.commit()

    # Auto-login après inscription
    session.permanent   = True
    session['user_id']  = user.id
    session['username'] = user.username
    session['email']    = user.email

    LOGIN_COUNT.labels(status='register_success').inc()
    return jsonify({'message': 'Compte créé', 'username': user.username}), 201


# ─────────────────────────────────────────────────────────────
#  API AUTH — CONNEXION  POST /api/auth/login
# ─────────────────────────────────────────────────────────────
@app.route('/api/auth/login', methods=['POST'])
def login():
    """
    Flux :
      1. Reçoit { email, password, remember }
      2. Cherche l'utilisateur par email
      3. Compare le mot de passe avec bcrypt.checkpw()
      4. Si OK → écrit user_id dans la session Flask
         → Flask envoie Set-Cookie: session=<id> au navigateur
         → Le navigateur le stocke et le renverra à chaque requête
      5. @login_required lira ce cookie pour identifier l'utilisateur

    Codes :
      200 → succès, session créée
      400 → champs manquants
      401 → email ou mot de passe incorrect
    """
    data = request.get_json()

    email    = (data.get('email')    or '').strip().lower()
    password = (data.get('password') or '').strip()
    remember = data.get('remember', False)

    if not email or not password:
        return jsonify({'error': 'Email et mot de passe requis'}), 400

    user = User.query.filter_by(email=email).first()

    # Anti-timing attack : on compare toujours, même si user n'existe pas
    dummy = '$2b$12$invalidhashtopreventtimingattackxxxxxxxxxxxxxxxxxxxxxxx'
    stored = user.password_hash if user else dummy
    password_ok = bcrypt.checkpw(password.encode('utf-8'), stored.encode('utf-8'))

    if not user or not password_ok:
        LOGIN_COUNT.labels(status='failed').inc()
        return jsonify({'error': 'Email ou mot de passe incorrect'}), 401

    session.permanent   = remember
    session['user_id']  = user.id
    session['username'] = user.username
    session['email']    = user.email

    user.last_login = db.func.now()
    db.session.commit()

    LOGIN_COUNT.labels(status='success').inc()
    return jsonify({'message': 'Connexion réussie', 'username': user.username}), 200


# ─────────────────────────────────────────────────────────────
#  API AUTH — QUI SUIS-JE  GET /api/auth/me
# ─────────────────────────────────────────────────────────────
@app.route('/api/auth/me', methods=['GET'])
def me():
    """Retourne l'utilisateur connecté. Le frontend l'appelle au chargement."""
    if 'user_id' not in session:
        return jsonify({'error': 'Non authentifié'}), 401
    return jsonify({
        'user_id':  session['user_id'],
        'username': session['username'],
        'email':    session['email'],
    }), 200


# ─────────────────────────────────────────────────────────────
#  API AUTH — DÉCONNEXION  POST /api/auth/logout
# ─────────────────────────────────────────────────────────────
@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'message': 'Déconnecté'}), 200


# ─────────────────────────────────────────────────────────────
#  API CHAT  POST /api/chat  (protégée — identique + user_id)
# ─────────────────────────────────────────────────────────────
@app.route('/api/chat', methods=['POST'])
@login_required
def chat():
    start = time.time()
    data         = request.get_json()
    session_id   = data.get('session_id', 'default')
    user_message = data.get('message', '').strip()
    user_id      = session['user_id']

    if not user_message:
        REQUEST_COUNT.labels(status='error').inc()
        return jsonify({'error': 'Message vide'}), 400

    conv = Conversation.query.filter_by(session_id=session_id, user_id=user_id).first()
    if not conv:
        conv = Conversation(session_id=session_id, user_id=user_id)
        db.session.add(conv)
        db.session.commit()

    db.session.add(Message(conversation_id=conv.id, role='user', content=user_message))
    db.session.commit()

    history  = Message.query.filter_by(conversation_id=conv.id).order_by(Message.id).all()
    messages = [{'role': m.role, 'content': m.content} for m in history]

    try:
        resp = requests.post(
            f'{OLLAMA_URL}/api/chat',
            json={'model': OLLAMA_MODEL, 'messages': messages, 'stream': False},
            timeout=300
        )
        resp.raise_for_status()
        result          = resp.json()
        assistant_reply = result['message']['content']
        TOKEN_COUNT.inc(result.get('eval_count', 0))
    except Exception as e:
        REQUEST_COUNT.labels(status='error').inc()
        return jsonify({'error': f'Ollama error: {str(e)}'}), 503

    db.session.add(Message(conversation_id=conv.id, role='assistant', content=assistant_reply))
    db.session.commit()

    REQUEST_COUNT.labels(status='success').inc()
    REQUEST_LATENCY.observe(time.time() - start)

    return jsonify({'reply': assistant_reply, 'session_id': session_id,
                    'conversation_id': conv.id})


# ─────────────────────────────────────────────────────────────
#  API HISTORIQUE + RESET (protégées — filtrées par user_id)
# ─────────────────────────────────────────────────────────────
@app.route('/api/history/<session_id>', methods=['GET'])
@login_required
def get_history(session_id):
    user_id = session['user_id']
    conv = Conversation.query.filter_by(session_id=session_id, user_id=user_id).first()
    if not conv:
        return jsonify({'messages': []})
    msgs = Message.query.filter_by(conversation_id=conv.id).order_by(Message.id).all()
    return jsonify({'messages': [{'role': m.role, 'content': m.content} for m in msgs]})


@app.route('/api/reset/<session_id>', methods=['DELETE'])
@login_required
def reset_conversation(session_id):
    user_id = session['user_id']
    conv = Conversation.query.filter_by(session_id=session_id, user_id=user_id).first()
    if conv:
        db.session.delete(conv)
        db.session.commit()
    return jsonify({'status': 'reset ok'})


# ─────────────────────────────────────────────────────────────
#  HEALTH + METRICS (identiques)
# ─────────────────────────────────────────────────────────────
@app.route('/metrics')
def metrics():
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}

@app.route('/health')
def health():
    return jsonify({
        'status': 'ok',
        'model':  OLLAMA_MODEL,
        'db':     'mysql',
        'authenticated': 'user_id' in session,
    })


# ─────────────────────────────────────────────────────────────
#  DÉMARRAGE
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    os.makedirs('/tmp/flask_sessions', exist_ok=True)
    with app.app_context():
        db.create_all()    # crée users + conversations + messages si absentes
    app.run(host='0.0.0.0', port=5000, debug=False)