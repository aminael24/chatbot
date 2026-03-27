from flask import Flask, request, jsonify, render_template
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST
import requests
import os
import time

app = Flask(__name__)
CORS(app)

# --- Config MySQL ---
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

# --- Prometheus Metrics ---
REQUEST_COUNT   = Counter('chatbot_requests_total', 'Total chat requests', ['status'])
REQUEST_LATENCY = Histogram('chatbot_request_duration_seconds', 'Request latency')
TOKEN_COUNT     = Counter('chatbot_tokens_total', 'Total tokens generated')

# --- Models ---
class Conversation(db.Model):
    __tablename__ = 'conversations'
    id         = db.Column(db.Integer, primary_key=True, autoincrement=True)
    session_id = db.Column(db.String(64), nullable=False, index=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    messages   = db.relationship('Message', backref='conversation',
                                 lazy=True, cascade='all, delete-orphan')

class Message(db.Model):
    __tablename__ = 'messages'
    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    conversation_id = db.Column(db.Integer, db.ForeignKey('conversations.id'), nullable=False)
    role            = db.Column(db.String(16), nullable=False)   # 'user' | 'assistant'
    content         = db.Column(db.Text(65535), nullable=False)
    created_at      = db.Column(db.DateTime, server_default=db.func.now())

# --- Routes ---
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/chat', methods=['POST'])
def chat():
    start = time.time()
    data         = request.get_json()
    session_id   = data.get('session_id', 'default')
    user_message = data.get('message', '').strip()

    if not user_message:
        REQUEST_COUNT.labels(status='error').inc()
        return jsonify({'error': 'Message vide'}), 400

    # Récupérer ou créer la conversation
    conv = Conversation.query.filter_by(session_id=session_id).first()
    if not conv:
        conv = Conversation(session_id=session_id)
        db.session.add(conv)
        db.session.commit()

    db.session.add(Message(conversation_id=conv.id, role='user', content=user_message))
    db.session.commit()

    # Construire l'historique pour Ollama
    history  = Message.query.filter_by(conversation_id=conv.id).order_by(Message.id).all()
    messages = [{'role': m.role, 'content': m.content} for m in history]

    # Appel Ollama
    try:
        resp = requests.post(
            f'{OLLAMA_URL}/api/chat',
            json={'model': OLLAMA_MODEL, 'messages': messages, 'stream': False},
            timeout=300
        )
        resp.raise_for_status()
        result         = resp.json()
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

@app.route('/api/history/<session_id>', methods=['GET'])
def get_history(session_id):
    conv = Conversation.query.filter_by(session_id=session_id).first()
    if not conv:
        return jsonify({'messages': []})
    msgs = Message.query.filter_by(conversation_id=conv.id).order_by(Message.id).all()
    return jsonify({'messages': [{'role': m.role, 'content': m.content} for m in msgs]})

@app.route('/api/reset/<session_id>', methods=['DELETE'])
def reset_conversation(session_id):
    conv = Conversation.query.filter_by(session_id=session_id).first()
    if conv:
        db.session.delete(conv)
        db.session.commit()
    return jsonify({'status': 'reset ok'})

@app.route('/metrics')
def metrics():
    return generate_latest(), 200, {'Content-Type': CONTENT_TYPE_LATEST}

@app.route('/health')
def health():
    return jsonify({'status': 'ok', 'model': OLLAMA_MODEL, 'db': 'mysql'})

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=False)
