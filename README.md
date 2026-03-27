# 🤖 CloudBot — Plateforme de Chatbot IA dans le Cloud
> **Projet 3 — GINF2 — Introduction au Cloud Computing**  
> ENSA Tanger · Université Abdelmalek Essaâdi

---

## 📋 Description

CloudBot est une plateforme de chatbot intelligent déployée sur infrastructure cloud open source, utilisant **MySQL** comme base de données, **Ollama** comme moteur LLM, et **Docker Compose** pour l'orchestration.

---

## 🏗️ Architecture

```
Internet
    │
    ▼
┌──────────────────────────────────────────────────────────┐
│  VM Ubuntu 22.04 (VirtualBox / OpenStack)                │
│                                                          │
│  ┌──────────┐    ┌──────────────┐    ┌───────────────┐  │
│  │  Nginx   │───▶│  Flask API   │───▶│    Ollama     │  │
│  │  :80     │    │  :5000       │    │  :11434       │  │
│  └──────────┘    └──────┬───────┘    │  (TinyLlama)  │  │
│                         │            └───────────────┘  │
│                         ▼                               │
│                  ┌──────────────┐                        │
│                  │  MySQL 8.0   │                        │
│                  │  :3306       │                        │
│                  └──────────────┘                        │
│                                                          │
│  ┌──────────────┐   ┌──────────┐   ┌─────────────────┐  │
│  │  Prometheus  │──▶│ Grafana  │   │  Node Exporter  │  │
│  │  :9090       │   │  :3000   │   │  :9100          │  │
│  └──────────────┘   └──────────┘   └─────────────────┘  │
│  ┌─────────────────┐                                     │
│  │  MySQL Exporter │                                     │
│  │  :9104          │                                     │
│  └─────────────────┘                                     │
└──────────────────────────────────────────────────────────┘
```

---

## 📂 Structure du projet

```
chatbot-cloud/
├── app/
│   ├── app.py              ← Backend Flask (API REST + métriques)
│   ├── templates/
│   │   └── index.html      ← Interface web
│   ├── requirements.txt    ← Flask, PyMySQL, prometheus-client...
│   ├── Dockerfile
│   └── entrypoint.sh       ← Attend MySQL avant démarrage
├── nginx/
│   └── nginx.conf
├── monitoring/
│   ├── prometheus/
│   │   └── prometheus.yml  ← Scrape Flask + Node + MySQL
│   └── grafana/
│       └── provisioning/
│           ├── datasources/ ← Prometheus auto-configuré
│           └── dashboards/  ← Dashboard MySQL auto-importé
├── db/
│   └── init.sql            ← Config charset utf8mb4
├── docker-compose.yml      ← 8 services orchestrés
├── deploy.sh               ← Déploiement en une commande
└── README.md
```

---

## 🚀 Déploiement

### 1. Installer Docker (Ubuntu 22.04)

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
```

### 2. Cloner et déployer

```bash
git clone <votre-repo>/chatbot-cloud.git
cd chatbot-cloud
chmod +x deploy.sh
./deploy.sh
```

### 3. Accès

| Interface    | URL                        | Identifiants       |
|-------------|----------------------------|--------------------|
| 🤖 ChatBot   | http://IP_VM               | —                  |
| 📊 Grafana   | http://IP_VM:3000          | admin / admin123   |
| 📈 Prometheus | http://IP_VM:9090          | —                  |

---

## ⚙️ Configuration MySQL

Les variables d'environnement dans `docker-compose.yml` :

```yaml
MYSQL_ROOT_PASSWORD: root_secret123
MYSQL_DATABASE:      chatbot
MYSQL_USER:          chatbot
MYSQL_PASSWORD:      chatbot123
```

### Accéder à MySQL en ligne de commande

```bash
# Depuis la VM hôte
docker compose exec db mysql -u chatbot -pchatbot123 chatbot

# Requêtes utiles
SHOW TABLES;
SELECT * FROM conversations ORDER BY created_at DESC LIMIT 10;
SELECT role, LEFT(content,80) FROM messages ORDER BY id DESC LIMIT 20;
```

### Schéma de la base de données

```sql
CREATE TABLE conversations (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  session_id VARCHAR(64) NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE messages (
  id              INT AUTO_INCREMENT PRIMARY KEY,
  conversation_id INT NOT NULL,
  role            VARCHAR(16) NOT NULL,   -- 'user' | 'assistant'
  content         TEXT NOT NULL,
  created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

---

## 📡 API REST

| Méthode  | Endpoint                  | Description                              |
|----------|---------------------------|------------------------------------------|
| POST     | `/api/chat`               | Envoie un message, retourne la réponse   |
| GET      | `/api/history/<session>`  | Historique d'une conversation            |
| DELETE   | `/api/reset/<session>`    | Réinitialise une conversation            |
| GET      | `/health`                 | Santé du service                         |
| GET      | `/metrics`                | Métriques Prometheus                     |

**Exemple :**
```bash
curl -X POST http://localhost/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Explique le cloud computing", "session_id": "test01"}'
```

---

## 📊 Monitoring

### Métriques applicatives (Flask)
- `chatbot_requests_total{status}` — compteur requêtes
- `chatbot_request_duration_seconds` — histogramme latence
- `chatbot_tokens_total` — tokens générés

### Métriques MySQL (mysqld_exporter)
- `mysql_global_status_threads_connected` — connexions actives
- `mysql_global_status_queries` — requêtes totales
- `mysql_global_status_slow_queries` — requêtes lentes
- `mysql_global_status_innodb_buffer_pool_*` — InnoDB buffer

### Métriques système (node_exporter)
- CPU, RAM, disque, réseau

---

## 🔒 Sécurité

- **Réseau isolé** : MySQL et Ollama non exposés publiquement
- **Pare-feu UFW** :
  ```bash
  sudo ufw enable
  sudo ufw allow ssh
  sudo ufw allow 80/tcp
  sudo ufw allow 3000/tcp
  sudo ufw allow 9090/tcp
  ```
- **Nginx** : headers X-Frame-Options, X-XSS-Protection, X-Content-Type-Options
- **MySQL** : utilisateur dédié, accès restreint au réseau Docker interne

---

## 🛠️ Commandes utiles

```bash
docker compose ps                          # État des services
docker compose logs -f app                 # Logs Flask
docker compose logs -f db                  # Logs MySQL
docker compose restart app                 # Redémarrer Flask
docker compose exec db mysqladmin -u chatbot -pchatbot123 status
docker compose down                        # Arrêt (volumes conservés)
docker compose down -v                     # Arrêt + suppression volumes
```

---

## 🎓 Concepts Cloud abordés

| Concept | Application dans le projet |
|---------|---------------------------|
| **IaaS** | VM VirtualBox/OpenStack avec Ubuntu 22.04 |
| **PaaS** | Docker Compose comme plateforme d'orchestration |
| **SaaS** | Interface web du chatbot accessible depuis le navigateur |
| **Conteneurisation** | Docker : isolation, portabilité, reproductibilité |
| **Microservices** | 8 services indépendants communiquant via réseau interne |
| **Monitoring** | Prometheus (collecte) + Grafana (visualisation) |
| **Base de données cloud** | MySQL 8.0 avec volume persistant |

---

*Projet GINF2 — Introduction au Cloud Computing — ENSA Tanger*
