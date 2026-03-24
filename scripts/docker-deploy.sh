#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
SERVICE_NAME="${SERVICE_NAME:-sandboxforge-mcp}"
ACTION="${1:-deploy}"

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD=(docker compose -f "${COMPOSE_FILE}")
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_CMD=(docker-compose -f "${COMPOSE_FILE}")
else
  echo "docker compose is required but not installed." >&2
  exit 1
fi

case "${ACTION}" in
  deploy|up)
    "${COMPOSE_CMD[@]}" up -d --build
    ;;
  down)
    "${COMPOSE_CMD[@]}" down
    ;;
  restart)
    "${COMPOSE_CMD[@]}" up -d --build --force-recreate
    ;;
  logs)
    "${COMPOSE_CMD[@]}" logs -f "${SERVICE_NAME}"
    ;;
  ps)
    "${COMPOSE_CMD[@]}" ps
    ;;
  pull)
    "${COMPOSE_CMD[@]}" pull
    ;;
  *)
    cat <<USAGE >&2
Usage: $(basename "$0") [deploy|up|down|restart|logs|ps|pull]

Environment:
  COMPOSE_FILE  Compose file path (default: docker-compose.yml)
  SERVICE_NAME  Service for logs (default: sandboxforge-mcp)
USAGE
    exit 1
    ;;
esac
