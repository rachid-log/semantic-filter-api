#!/usr/bin/env bash
#
# Ops helper for the semantic-filter-api stack on your VPS.
#
# Connection details come from .env.deploy (gitignored, chmod 600, your machine
# only), or from variables exported in your shell — see scripts/lib/deploy-env.sh.
# Nothing here touches the application's .env.
#
#   ./scripts/vps.sh health          what the running container reports
#   ./scripts/vps.sh ps              docker compose ps
#   ./scripts/vps.sh logs [service]  follow logs (ctrl-c to stop)
#   ./scripts/vps.sh restart         restart the app container
#   ./scripts/vps.sh rollback        swap back to semantic-filter-api:previous
#   ./scripts/vps.sh cert            certificate issuer and dates
#   ./scripts/vps.sh traefik         Traefik's view — ACME and routing errors
#   ./scripts/vps.sh ssh [command]   run anything in the app directory

set -euo pipefail

APP="semantic-filter-api"
SVC="semantic-filter-api"
PORT="8080"
# Your Traefik container's name (`docker ps` on the server shows it).
TRAEFIK_CONTAINER="${TRAEFIK_CONTAINER:-root-traefik-1}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() { sed -n '3,17p' "${BASH_SOURCE[0]}"; }

case "${1:-}" in
  -h|--help|help) usage; exit 0 ;;
esac

# The public hostname comes from SITE_URL: the local .env when there is one,
# else the committed .env.example. One place to change, not three.
ENV_SRC="$ROOT/.env"; [ -f "$ENV_SRC" ] || ENV_SRC="$ROOT/.env.example"
DOMAIN=$(grep '^SITE_URL=' "$ENV_SRC" | cut -d= -f2- | sed -E 's#^https?://##; s#/.*$##')

# shellcheck source=lib/deploy-env.sh
. "$ROOT/scripts/lib/deploy-env.sh"
load_deploy_env "$APP" "$ROOT"

ssh_run() {
  ssh -i "$KEY_PATH" -p "$VPS_SSH_PORT" \
      -o StrictHostKeyChecking=accept-new \
      "$VPS_USER@$VPS_HOST" "$@"
}

# Interactive variants need a TTY allocated.
ssh_tty() {
  ssh -t -i "$KEY_PATH" -p "$VPS_SSH_PORT" \
      -o StrictHostKeyChecking=accept-new \
      "$VPS_USER@$VPS_HOST" "$@"
}

cmd="${1:-health}"
shift || true

case "$cmd" in
  health)
    echo "── container ──"
    ssh_run "docker inspect -f '{{.State.Health.Status}}' $SVC 2>/dev/null || echo 'no healthcheck / not running'"
    echo "── /api/health (bypasses DNS and Traefik) ──"
    ssh_run "docker exec $SVC wget -qO- http://127.0.0.1:$PORT/api/health || true"
    echo
    echo "── public ($DOMAIN) ──"
    curl -sI "https://$DOMAIN/api/health" | head -1 || true
    ;;

  ps)
    ssh_run "cd $VPS_APP_DIR && docker compose ps"
    ;;

  logs)
    ssh_tty "cd $VPS_APP_DIR && docker compose logs -f --tail=100 ${1:-$SVC}"
    ;;

  restart)
    # Also the way to trigger a fresh ACME attempt after a DNS fix.
    ssh_run "cd $VPS_APP_DIR && docker compose restart $SVC"
    ;;

  rollback)
    # Survives only until the next deploy overwrites the tag.
    echo "Rolling $SVC back to $APP:previous."
    read -r -p "Continue? [y/N] " ok
    [ "$ok" = "y" ] || { echo "aborted"; exit 1; }
    ssh_run "cd $VPS_APP_DIR && docker image tag $APP:previous $APP:latest && docker compose up -d --force-recreate $SVC"
    ;;

  cert)
    echo | openssl s_client -connect "$DOMAIN:443" -servername "$DOMAIN" 2>/dev/null \
      | openssl x509 -noout -issuer -dates
    echo "(want: O = Let's Encrypt. 'TRAEFIK DEFAULT CERT' means ACME failed — see: vps.sh traefik)"
    ;;

  traefik)
    # Certificate and routing failures appear here, never in the app's logs.
    ssh_run "docker logs --tail=200 $TRAEFIK_CONTAINER 2>&1 | grep -iE 'acme|error|$SVC' || echo 'nothing matching'"
    ;;

  ssh)
    if [ $# -eq 0 ]; then
      ssh_tty "cd $VPS_APP_DIR && exec \$SHELL -l"
    else
      ssh_run "cd $VPS_APP_DIR && $*"
    fi
    ;;

  *)
    echo "unknown command: $cmd" >&2
    usage
    exit 1
    ;;
esac
