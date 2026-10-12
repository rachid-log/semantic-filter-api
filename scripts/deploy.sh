#!/usr/bin/env bash
#
# Deploy semantic-filter-api to your VPS by hand.
#
# The normal path is a push to `main` — .github/workflows/deploy.yml does all of
# this, and renders .env from repository secrets, which this script cannot do.
# Use this when Actions is unavailable, or to redeploy the current `main` without
# an empty commit.
#
# Same order as the pipeline, for the same reasons: build while the old
# container still serves, swap, verify against what is actually being served,
# prune only dangling layers.
#
#   ./scripts/deploy.sh              deploy origin/main as it stands on GitHub
#   ./scripts/deploy.sh --push-env   ALSO upload this machine's .env (see below)
#   ./scripts/deploy.sh --ref BRANCH deploy some other branch
#
# Connection details come from .env.deploy, or from variables exported in your
# shell — see scripts/lib/deploy-env.sh.

set -euo pipefail

APP="semantic-filter-api"
SVC="semantic-filter-api"
PORT="8080"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

PUSH_ENV="no"
REF="main"
while [ $# -gt 0 ]; do
  case "$1" in
    --push-env) PUSH_ENV="yes"; shift ;;
    --ref)      REF="${2:?--ref needs a branch}"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

# shellcheck source=lib/deploy-env.sh
. "$ROOT/scripts/lib/deploy-env.sh"
load_deploy_env "$APP" "$ROOT"

SSH=(ssh -i "$KEY_PATH" -p "$VPS_SSH_PORT" -o StrictHostKeyChecking=accept-new "$VPS_USER@$VPS_HOST")

# A private repo needs a token to fetch; a public one does not. Passed inline,
# the same way the Actions pipeline does it, so it never lands in .git/config.
if [ -n "${GH_PAT:-}" ]; then
  REPO_URL="https://x-access-token:${GH_PAT}@github.com/${GH_REPO}.git"
else
  REPO_URL="https://github.com/${GH_REPO}.git"
fi

# The commit the server is expected to end up on, resolved here so the
# verification below checks against something this script did not itself report.
EXPECTED_SHA=$(git ls-remote "$REPO_URL" "refs/heads/$REF" | cut -c1-12)
[ -n "$EXPECTED_SHA" ] || { echo "error: could not resolve $REF on $GH_REPO (private repo without GH_PAT?)" >&2; exit 1; }
echo "credentials from: $DEPLOY_ENV_SOURCE"
echo "deploying $GH_REPO@$REF ($EXPECTED_SHA) to $VPS_USER@$VPS_HOST:$VPS_APP_DIR"

# Uploading .env is opt-in and deliberately awkward. The pipeline renders it from
# repository secrets; a local .env is a *development* file, and pushing it
# silently is how a test token reaches production.
ENV_B64=""
if [ "$PUSH_ENV" = "yes" ]; then
  [ -f "$ROOT/.env" ] || { echo "error: --push-env but no .env here" >&2; exit 1; }
  # Anchored to an assignment: the unanchored form also matches the comments
  # in .env.example that explain the sentinel, and a local .env copied from it
  # keeps those comments. A surviving placeholder is always a value.
  if grep -qE '^[A-Za-z0-9_]+=.*__REQUIRED__' "$ROOT/.env"; then
    echo "error: .env still contains __REQUIRED__ placeholders" >&2
    grep -nE '^[A-Za-z0-9_]+=.*__REQUIRED__' "$ROOT/.env" >&2
    exit 1
  fi
  echo
  echo "--push-env will OVERWRITE $VPS_APP_DIR/.env with this machine's .env."
  grep -oE '^[A-Za-z0-9_]+=' "$ROOT/.env" | tr -d '=' | tr '\n' ' '
  echo
  read -r -p "Overwrite the production .env with these keys? [y/N] " ok
  [ "$ok" = "y" ] || { echo "aborted"; exit 1; }
  ENV_B64=$(base64 -w 0 "$ROOT/.env")
fi

"${SSH[@]}" bash -s -- "$APP" "$SVC" "$PORT" "$VPS_APP_DIR" "$REF" "$REPO_URL" "$ENV_B64" <<'REMOTE'
set -e
APP="$1"; SVC="$2"; PORT="$3"; APP_DIR="$4"; REF="$5"; REPO_URL="$6"; ENV_B64="$7"

cd "$APP_DIR" || { echo "no $APP_DIR on this server — run the Actions workflow once to create it"; exit 1; }

# -fd, not -fdx: -x would delete the gitignored .env this stack runs on.
git fetch --depth 1 "$REPO_URL" "$REF"
git reset --hard FETCH_HEAD
git clean -fd

if [ -n "$ENV_B64" ]; then
  echo "$ENV_B64" | base64 --decode > .env
  chmod 600 .env
  echo "wrote .env"
fi

[ -f .env ] || { echo "no .env on the server — deploy from Actions once, or use --push-env"; exit 1; }

# Rollback tag. Fails harmlessly on a first deploy.
docker image tag "$APP:latest" "$APP:previous" || true

# The old container is still serving through this. A build error is a failed
# script, not a downed service.
docker compose build

docker compose up -d --force-recreate "$SVC"

# Artifact guard: what is actually being served, not what the config says.
HEALTH=""
for i in 1 2 3 4 5 6 7 8 9 10; do
  HEALTH=$(docker exec "$SVC" wget -qO- "http://127.0.0.1:$PORT/api/health" 2>/dev/null || true)
  [ -n "$HEALTH" ] && break
  sleep 3
done
if [ -z "$HEALTH" ]; then
  # wget discards the body on any non-2xx, so a crash and a non-2xx answer are
  # indistinguishable here. The logs tell them apart.
  echo "NO HEALTH RESPONSE — container failed to start, or answered non-2xx"
  docker compose logs --tail=80 "$SVC"
  exit 1
fi

field() { echo "$HEALTH" | grep -o "\"$1\":\"\?[^,\"}]*" | cut -d: -f2- | tr -d '"'; }
EXPECTED_URL=$(grep '^SITE_URL=' .env | cut -d= -f2-)
EXPECTED_MODEL=$(grep '^EMBED_MODEL=' .env | cut -d= -f2-)
SERVED_URL=$(field siteUrl)
SERVED_SHA=$(field commit)

FAIL=0
[ "$SERVED_URL" = "$EXPECTED_URL" ] || { echo "RUNTIME DOMAIN MISMATCH — '$SERVED_URL' vs '$EXPECTED_URL'"; FAIL=1; }
[ "$SERVED_SHA" = "$(git rev-parse HEAD | cut -c1-12)" ] || { echo "COMMIT MISMATCH — health says '$SERVED_SHA'"; FAIL=1; }
SERVED_BAKED=$(docker exec "$SVC" wget -qO- "http://127.0.0.1:$PORT/robots.txt" 2>/dev/null | awk '/^Host:/{print $2}' || true)
[ "$SERVED_BAKED" = "$EXPECTED_URL" ] || { echo "BAKED DOMAIN MISMATCH — robots.txt says '$SERVED_BAKED'"; FAIL=1; }
[ "$(field auth)" = "configured" ] || { echo "EMBED_API_TOKEN MISSING — /embed would refuse every request"; FAIL=1; }
[ "$(field model)" = "$EXPECTED_MODEL" ] || { echo "MODEL MISMATCH — health says '$(field model)', expected '$EXPECTED_MODEL'"; FAIL=1; }

if [ "$FAIL" -ne 0 ]; then
  echo "Roll back with:  cd $APP_DIR && docker image tag $APP:previous $APP:latest && docker compose up -d --force-recreate $SVC"
  exit 1
fi

echo "verified: $SERVED_SHA on $SERVED_URL"

# Dangling layers only. `system prune -a -f` would delete $APP:previous and the
# whole layer cache, for every stack on this server.
docker image prune -f
echo "done"
REMOTE

echo
echo "expected commit: $EXPECTED_SHA"
echo "verify:  ./scripts/vps.sh health && ./scripts/vps.sh cert"
