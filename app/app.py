from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_session import Session
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
from functools import wraps
import requests
import bcrypt
import os
import time
from datetime import timedelta

app = Flask(__name__)
CORS(app, supports_credentials=True)

# ─────────────────────────────────────────────────────────────
#  CONFIG SESSION
# ─────────────────────────────────────────────────────────────
app.secret_key = os.getenv('SECRET_KEY', 'cloudbot-ginf2-ensa-tanger-2026')
app.config['SESSION_TYPE']               = 'filesystem'
app.config['SESSION_FILE_DIR']           = '/tmp/flask_sessions'
app.config['SESSION_PERMANENT']          = True
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=24)
app.config['SESSION_COOKIE_HTTPONLY']    = True
app.config['SESSION_COOKIE_SAMESITE']   = 'Lax'

Session(app)

# ─────────────────────────────────────────────────────────────
#  CONFIG MYSQL
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
#  PROMETHEUS METRICS
# ─────────────────────────────────────────────────────────────
REQUEST_COUNT   = Counter('chatbot_requests_total', 'Total chat requests', ['status'])
REQUEST_LATENCY = Histogram('chatbot_request_duration_seconds', 'Request latency')
TOKEN_COUNT     = Counter('chatbot_tokens_total', 'Total tokens generated')
LOGIN_COUNT     = Counter('chatbot_login_total', 'Login attempts', ['status'])

# ─────────────────────────────────────────────────────────────
#  MODÈLES
# ─────────────────────────────────────────────────────────────
class User(db.Model):
    __tablename__ = 'users'

    id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username      = db.Column(db.String(80),  nullable=False)
    email         = db.Column(db.String(120), nullable=False, unique=True)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at    = db.Column(db.DateTime, server_default=db.func.now())
    last_login    = db.Column(db.DateTime, nullable=True)

    conversations = db.relationship('Conversation', backref='user',
                                    lazy=True, cascade='all, delete-orphan')

    def set_password(self, password: str):
        self.password_hash = bcrypt.hashpw(
            password.encode('utf-8'),
            bcrypt.gensalt()
        ).decode('utf-8')

    def check_password(self, password: str) -> bool:
        return bcrypt.checkpw(
            password.encode('utf-8'),
            self.password_hash.encode('utf-8')
        )


class Conversation(db.Model):
    __tablename__ = 'conversations'

    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    session_id = db.Column(db.String(64), nullable=False, index=True)
    title      = db.Column(db.String(100), nullable=True)   # ← titre de la conversation
    user_id    = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())
    messages   = db.relationship('Message', backref='conversation',
                                 lazy=True, cascade='all, delete-orphan')


class Message(db.Model):
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
#  API AUTH — INSCRIPTION
# ─────────────────────────────────────────────────────────────
@app.route('/api/auth/register', methods=['POST'])
def register():
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
    user.set_password(password)
    db.session.add(user)
    db.session.commit()

    session.permanent   = True
    session['user_id']  = user.id
    session['username'] = user.username
    session['email']    = user.email

    LOGIN_COUNT.labels(status='register_success').inc()
    return jsonify({'message': 'Compte créé', 'username': user.username}), 201


# ─────────────────────────────────────────────────────────────
#  API AUTH — CONNEXION
# ─────────────────────────────────────────────────────────────
@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json()

    email    = (data.get('email')    or '').strip().lower()
    password = (data.get('password') or '').strip()
    remember = data.get('remember', False)

    if not email or not password:
        return jsonify({'error': 'Email et mot de passe requis'}), 400

    user = User.query.filter_by(email=email).first()

    dummy       = '$2b$12$invalidhashtopreventtimingattackxxxxxxxxxxxxxxxxxxxxxxx'
    stored      = user.password_hash if user else dummy
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
#  API AUTH — ME + LOGOUT
# ─────────────────────────────────────────────────────────────
@app.route('/api/auth/me', methods=['GET'])
def me():
    if 'user_id' not in session:
        return jsonify({'error': 'Non authentifié'}), 401
    return jsonify({
        'user_id':  session['user_id'],
        'username': session['username'],
        'email':    session['email'],
    }), 200

@app.route('/api/auth/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'message': 'Déconnecté'}), 200


# ─────────────────────────────────────────────────────────────
#  API SESSIONS — liste toutes les conversations de l'utilisateur
# ─────────────────────────────────────────────────────────────
@app.route('/api/sessions', methods=['GET'])
@login_required
def get_sessions():
    user_id = session['user_id']
    convs = (
        Conversation.query
        .filter_by(user_id=user_id)
        .order_by(Conversation.updated_at.desc())
        .all()
    )
    result = []
    for c in convs:
        # Titre = titre sauvegardé OU premier message utilisateur
        title = c.title
        if not title:
            first_msg = (
                Message.query
                .filter_by(conversation_id=c.id, role='user')
                .order_by(Message.id)
                .first()
            )
            title = first_msg.content[:50] if first_msg else 'Nouvelle conversation'

        result.append({
            'id':         c.session_id,
            'title':      title,
            'created_at': c.created_at.isoformat() if c.created_at else None,
            'updated_at': c.updated_at.isoformat() if c.updated_at else None,
        })

    return jsonify({'sessions': result})


# ─────────────────────────────────────────────────────────────
#  API SESSIONS — mettre à jour le titre d'une conversation
# ─────────────────────────────────────────────────────────────
@app.route('/api/sessions/<session_id>/title', methods=['PATCH'])
@login_required
def update_session_title(session_id):
    user_id = session['user_id']
    data    = request.get_json()
    title   = (data.get('title') or '').strip()[:100]

    conv = Conversation.query.filter_by(session_id=session_id, user_id=user_id).first()
    if not conv:
        return jsonify({'error': 'Conversation introuvable'}), 404

    conv.title = title
    db.session.commit()
    return jsonify({'status': 'ok', 'title': title})


# ─────────────────────────────────────────────────────────────
#  API CHAT
# ─────────────────────────────────────────────────────────────
@app.route('/api/chat', methods=['POST'])
@login_required
def chat():
    start        = time.time()
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

    # Sauvegarder le titre automatiquement sur le 1er message
    if not conv.title:
        conv.title = user_message[:50]

    db.session.add(Message(conversation_id=conv.id, role='user', content=user_message))
    db.session.commit()

    history  = Message.query.filter_by(conversation_id=conv.id).order_by(Message.id).all()
    messages = [
        {
            'role': 'system',
            'content': (
                'Tu es CloudBot, un assistant IA intelligent et précis. '
                'Réponds toujours en français, de manière claire et concise. '
                'Ne réponds qu\'à ce qui est demandé. '
                'Utilise "vous" pour t\'adresser à l\'utilisateur.'
            )
        }
    ] + [{'role': m.role, 'content': m.content} for m in history]

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

    # Mettre à jour updated_at pour trier par activité récente
    conv.updated_at = db.func.now()
    db.session.commit()

    REQUEST_COUNT.labels(status='success').inc()
    REQUEST_LATENCY.observe(time.time() - start)

    return jsonify({
        'reply':           assistant_reply,
        'session_id':      session_id,
        'conversation_id': conv.id,
    })


# ─────────────────────────────────────────────────────────────
#  API HISTORIQUE + RESET
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
#  HEALTH + METRICS
# ─────────────────────────────────────────────────────────────
@app.route('/metrics')
def metrics():
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}

@app.route('/health')
def health():
    return jsonify({
        'status':        'ok',
        'model':         OLLAMA_MODEL,
        'db':            'mysql',
        'authenticated': 'user_id' in session,
    })


# ─────────────────────────────────────────────────────────────
#  DÉMARRAGE
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    os.makedirs('/tmp/flask_sessions', exist_ok=True)
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=False)