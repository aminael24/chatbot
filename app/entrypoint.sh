#!/bin/sh
set -e

echo "Attente de la base de donnees MySQL..."
until python -c "
import pymysql, os
pymysql.connect(
    host=os.getenv('MYSQL_HOST','db'),
    port=int(os.getenv('MYSQL_PORT','3306')),
    user=os.getenv('MYSQL_USER','chatbot'),
    password=os.getenv('MYSQL_PASSWORD','chatbot123'),
    database=os.getenv('MYSQL_DB','chatbot')
)
" 2>/dev/null; do
  echo "MySQL pas encore pret, nouvelle tentative dans 3s..."
  sleep 3
done

echo "MySQL pret !"

python -c "
from app import app, db
with app.app_context():
    db.create_all()
"

echo "Demarrage..."
exec gunicorn --bind 0.0.0.0:5000 --workers 2 --timeout 120 app:app