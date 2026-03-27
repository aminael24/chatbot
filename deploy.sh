#!/bin/bash
# ═══════════════════════════════════════════════════════════
#  CloudBot — Script de déploiement automatique (MySQL)
#  Projet Cloud Computing — ENSA Tanger / GINF2
# ═══════════════════════════════════════════════════════════
set -e

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[✗]${NC} $1"; exit 1; }
section() { echo -e "\n${CYAN}══════════════════════════════════════${NC}"; echo -e "${CYAN}  $1${NC}"; echo -e "${CYAN}══════════════════════════════════════${NC}"; }

section "🚀 Déploiement CloudBot (MySQL)"

# ── Prérequis ────────────────────────────────────────────────────────────
section "1. Vérification des prérequis"
command -v docker >/dev/null 2>&1 || error "Docker n'est pas installé."
docker compose version >/dev/null 2>&1 || error "Docker Compose v2 requis."
info "Docker $(docker --version | cut -d' ' -f3 | tr -d ',')"
info "Docker Compose $(docker compose version --short)"

# ── Ports ────────────────────────────────────────────────────────────────
section "2. Ports qui seront exposés"
echo "  • 80    → Interface ChatBot (Nginx)"
echo "  • 3000  → Grafana  (admin / admin123)"
echo "  • 9090  → Prometheus"
echo "  • 9100  → Node Exporter"
echo "  • 9104  → MySQL Exporter"

# ── Build & start ─────────────────────────────────────────────────────────
section "3. Construction des images Docker"
docker compose build --no-cache
info "Images construites"

section "4. Démarrage des services"
docker compose up -d
info "Conteneurs démarrés"

# ── Attente des services ──────────────────────────────────────────────────
section "5. Vérification de la disponibilité"

wait_http() {
  local name=$1 url=$2 max=40 n=0
  echo -n "  Attente de $name "
  until curl -sf "$url" >/dev/null 2>&1 || [ $n -ge $max ]; do
    echo -n "."; sleep 3; n=$((n+1))
  done
  [ $n -ge $max ] && warn "$name pas encore prêt" || echo " ✅"
}

sleep 15
wait_http "Flask App"  "http://localhost/health"
wait_http "Prometheus" "http://localhost:9090/-/ready"
wait_http "Grafana"    "http://localhost:3000/api/health"

# ── Résumé ────────────────────────────────────────────────────────────────
section "6. Déploiement terminé 🎉"
IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "localhost")
echo ""
echo -e "  ${GREEN}🌐  ChatBot   →  http://$IP${NC}"
echo -e "  ${CYAN}📊  Grafana   →  http://$IP:3000   (admin / admin123)${NC}"
echo -e "  ${CYAN}📈  Prometheus→  http://$IP:9090${NC}"
echo ""
echo "  Commandes utiles :"
echo "    docker compose logs -f app        # Logs Flask"
echo "    docker compose logs -f db         # Logs MySQL"
echo "    docker compose exec db mysql -u chatbot -pchatbot123 chatbot"
echo "    docker compose ps                 # État des conteneurs"
echo "    docker compose down               # Arrêt (données conservées)"
echo "    docker compose down -v            # Arrêt + suppression volumes"
