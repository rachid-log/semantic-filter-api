#!/usr/bin/env bash
#
# Loads the VPS access credentials for semantic-filter-api.
#
# Sourced by scripts/deploy.sh and scripts/vps.sh. Not runnable on its own, and
# never referenced by docker-compose.yml — nothing in here reaches the running
# container.
#
# Two sources, checked in this order:
#
#   1. .env.deploy in the repo root. Gitignored, chmod 600, this machine only.
#      Wins whenever it exists, so a local override is always possible.
#
#   2. Variables already exported in your shell. Use this when the values live
#      in your secret store (a password manager, a vault) and you would rather
#      not write them to disk: export them for one command, then they are gone.
#
#        VPS_HOST, VPS_USER                      required
#        VPS_SSH_PORT, VPS_SSH_KEY, VPS_APP_DIR  optional (defaults below)
#        GH_REPO                                 required (owner/repo)
#        GH_PAT                                  only for a private repo

# load_deploy_env <app-slug> <repo-root>
#
# Populates VPS_HOST, VPS_USER, VPS_SSH_PORT, VPS_SSH_KEY, VPS_APP_DIR, GH_REPO
# and (when set) GH_PAT, and sets DEPLOY_ENV_SOURCE to where they came from.
load_deploy_env() {
  local app="${1:?load_deploy_env needs the app slug}"
  local root="${2:?load_deploy_env needs the repo root}"
  local env_deploy="$root/.env.deploy"

  if [ -f "$env_deploy" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$env_deploy"
    set +a
    DEPLOY_ENV_SOURCE=".env.deploy"
  else
    DEPLOY_ENV_SOURCE="environment"
  fi

  if [ -z "${VPS_HOST:-}" ] || [ -z "${VPS_USER:-}" ]; then
    echo "error: VPS_HOST / VPS_USER missing (looked in $DEPLOY_ENV_SOURCE)." >&2
    echo "  Either:  cp .env.deploy.example .env.deploy && chmod 600 .env.deploy   (then fill it in)" >&2
    echo "  Or:      export VPS_HOST=... VPS_USER=... GH_REPO=...   from your secret store" >&2
    return 1
  fi

  VPS_SSH_PORT="${VPS_SSH_PORT:-22}"
  VPS_SSH_KEY="${VPS_SSH_KEY:-$HOME/.ssh/id_ed25519}"
  # Same place the Actions workflow uses: $HOME/<app> of the SSH user.
  if [ -z "${VPS_APP_DIR:-}" ]; then
    if [ "$VPS_USER" = "root" ]; then VPS_APP_DIR="/root/$app"; else VPS_APP_DIR="/home/$VPS_USER/$app"; fi
  fi

  : "${GH_REPO:?GH_REPO missing — set it (owner/repo) in .env.deploy or your shell}"

  export VPS_HOST VPS_USER VPS_SSH_PORT VPS_SSH_KEY VPS_APP_DIR GH_REPO
  if [ -n "${GH_PAT:-}" ]; then export GH_PAT; fi

  # `~` in a dotenv value is a literal tilde, not $HOME.
  KEY_PATH="${VPS_SSH_KEY/#\~/$HOME}"
  if [ ! -f "$KEY_PATH" ]; then
    echo "error: SSH key not found at $KEY_PATH (VPS_SSH_KEY=$VPS_SSH_KEY, from $DEPLOY_ENV_SOURCE)" >&2
    echo "  This is the key's PATH. The private key has to already exist on this machine." >&2
    return 1
  fi
  export KEY_PATH
}
