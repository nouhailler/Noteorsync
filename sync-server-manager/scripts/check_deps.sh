#!/usr/bin/env bash
set -uo pipefail

SYNC_DIR="${1:-./sync-server}"
PORT="${2:-8787}"
JSON_MODE="${3:-}"

check_cmd() {
  command -v "$1" >/dev/null 2>&1
}

DOCKER_BIN_OK=false
DOCKER_BIN_DETAIL="Introuvable dans PATH"
if check_cmd docker; then
  DOCKER_BIN_OK=true
  DOCKER_BIN_DETAIL="$(docker --version 2>/dev/null || echo 'docker présent')"
fi

DOCKER_DAEMON_OK=false
DOCKER_DAEMON_DETAIL="Démon Docker non démarré ou permissions insuffisantes"
if [ "$DOCKER_BIN_OK" = true ]; then
  if docker info >/dev/null 2>&1; then
    DOCKER_DAEMON_OK=true
    DOCKER_DAEMON_DETAIL="Démon Docker accessible"
  else
    ERR_MSG="$(docker info 2>&1 | head -n 1 || true)"
    if [ -n "$ERR_MSG" ]; then
      DOCKER_DAEMON_DETAIL="$ERR_MSG"
    fi
  fi
fi

COMPOSE_OK=false
COMPOSE_CMD=""
COMPOSE_DETAIL="Plugin 'docker compose' introuvable"
if [ "$DOCKER_BIN_OK" = true ] && docker compose version >/dev/null 2>&1; then
  COMPOSE_OK=true
  COMPOSE_CMD="docker compose"
  COMPOSE_DETAIL="$(docker compose version 2>/dev/null | head -n 1)"
elif check_cmd docker-compose; then
  COMPOSE_OK=true
  COMPOSE_CMD="docker-compose"
  COMPOSE_DETAIL="$(docker-compose --version 2>/dev/null | head -n 1)"
fi

CURL_OK=false
CURL_DETAIL="curl introuvable"
if check_cmd curl; then
  CURL_OK=true
  CURL_DETAIL="$(curl --version 2>/dev/null | head -n 1)"
fi

SYNC_FILES_OK=false
SYNC_FILES_DETAIL="Dossier sync-server/ incomplet ($SYNC_DIR)"
if [ -f "$SYNC_DIR/compose.yaml" ] && [ -f "$SYNC_DIR/Dockerfile" ] && [ -f "$SYNC_DIR/pyproject.toml" ] && [ -f "$SYNC_DIR/app/main.py" ]; then
  SYNC_FILES_OK=true
  SYNC_FILES_DETAIL="compose.yaml, Dockerfile, pyproject.toml et app/main.py détectés"
fi

PORT_STATUS="free"
PORT_DETAIL="Port $PORT disponible"
if check_cmd ss; then
  if ss -ltn 2>/dev/null | grep -qE "[:.]${PORT}[[:space:]]"; then
    PORT_STATUS="in_use"
    PORT_DETAIL="Port $PORT déjà utilisé (peut être le conteneur Noteor Sync actif)"
  fi
fi

ALL_READY=false
if [ "$DOCKER_BIN_OK" = true ] && [ "$DOCKER_DAEMON_OK" = true ] && [ "$COMPOSE_OK" = true ] && [ "$CURL_OK" = true ] && [ "$SYNC_FILES_OK" = true ]; then
  ALL_READY=true
fi

if [ "$JSON_MODE" = "--json" ]; then
  python3 - "$DOCKER_BIN_OK" "$DOCKER_BIN_DETAIL" \
             "$DOCKER_DAEMON_OK" "$DOCKER_DAEMON_DETAIL" \
             "$COMPOSE_OK" "$COMPOSE_CMD" "$COMPOSE_DETAIL" \
             "$CURL_OK" "$CURL_DETAIL" \
             "$SYNC_FILES_OK" "$SYNC_FILES_DETAIL" \
             "$PORT_STATUS" "$PORT_DETAIL" \
             "$ALL_READY" <<'PY'
import json, sys
b = lambda s: s == "true"
payload = {
    "all_ready": b(sys.argv[14]),
    "compose_cmd": sys.argv[6],
    "checks": [
        {"id": "docker_cli", "label": "Docker CLI", "ok": b(sys.argv[1]), "detail": sys.argv[2], "fix": "sudo apt install -y docker.io"},
        {"id": "docker_daemon", "label": "Démon Docker & droits", "ok": b(sys.argv[3]), "detail": sys.argv[4], "fix": "sudo systemctl enable --now docker && sudo usermod -aG docker $USER"},
        {"id": "docker_compose", "label": "Docker Compose", "ok": b(sys.argv[5]), "detail": sys.argv[7], "fix": "sudo apt install -y docker-compose-v2 || sudo apt install -y docker-compose-plugin"},
        {"id": "curl", "label": "curl (contrôle /healthz)", "ok": b(sys.argv[8]), "detail": sys.argv[9], "fix": "sudo apt install -y curl"},
        {"id": "sync_dir", "label": "Fichiers sync-server/", "ok": b(sys.argv[10]), "detail": sys.argv[11], "fix": "Sélectionnez le dossier sync-server/ du dépôt Noteor"},
        {"id": "port", "label": "Port local", "ok": True, "warning": sys.argv[12] == "in_use", "detail": sys.argv[13], "fix": ""},
    ],
}
print(json.dumps(payload, ensure_ascii=False))
PY
  exit 0
fi

echo "=== Vérification des dépendances Noteor Sync ==="
echo "[docker]         $DOCKER_BIN_OK - $DOCKER_BIN_DETAIL"
echo "[docker daemon]  $DOCKER_DAEMON_OK - $DOCKER_DAEMON_DETAIL"
echo "[docker compose] $COMPOSE_OK - $COMPOSE_DETAIL"
echo "[curl]           $CURL_OK - $CURL_DETAIL"
echo "[sync-server]    $SYNC_FILES_OK - $SYNC_FILES_DETAIL"
echo "[port $PORT]      $PORT_STATUS - $PORT_DETAIL"
if [ "$ALL_READY" = true ]; then
  echo "STATUT: PRET"
  exit 0
else
  echo "STATUT: DEPENDANCES MANQUANTES"
  exit 1
fi
