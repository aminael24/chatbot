-- ============================================================
--  CloudBot — Initialisation MySQL
--  Exécuté automatiquement au premier démarrage du conteneur
--  Note : les tables sont créées par SQLAlchemy (db.create_all())
--  Ce script configure uniquement les paramètres MySQL globaux
-- ============================================================

CREATE DATABASE IF NOT EXISTS chatbot
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE chatbot;

SET GLOBAL max_allowed_packet  = 67108864;   -- 64 Mo (réponses LLM longues)
SET GLOBAL wait_timeout        = 300;
SET GLOBAL interactive_timeout = 300;

-- Confirmation
SELECT 'Base de données CloudBot initialisée ✅' AS status;

-- ──────────────────────────────────────────────────────────────
--  TABLES (créées automatiquement par SQLAlchemy au démarrage
--  de Flask via db.create_all() dans entrypoint.sh)
--
--  users         → id, username, email, password_hash, created_at, last_login
--  conversations → id, session_id, user_id (FK→users), created_at
--  messages      → id, conversation_id (FK→conversations), role, content, created_at
-- ──────────────────────────────────────────────────────────────