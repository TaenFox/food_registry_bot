#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/../food_registry_bot_local/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Файл окружения не найден: $ENV_FILE" >&2
  echo "Создай его через ./scripts/init-dev-secrets.sh или подготовь вручную." >&2
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

sanitize_worktree_token() {
  printf '%s' "$1" \
    | tr '[:upper:]' '[:lower:]' \
    | tr -cs 'a-z0-9' '-' \
    | sed 's/^-*//; s/-*$//'
}

derive_worktree_postgres_port() {
  local token="$1"
  local checksum
  checksum="$(printf '%s' "$token" | cksum | awk '{print $1}')"
  printf '%s' "$((20000 + (checksum % 10000)))"
}

git_dir="$(git -C "$PROJECT_ROOT" rev-parse --path-format=absolute --git-dir)"
git_common_dir="$(git -C "$PROJECT_ROOT" rev-parse --path-format=absolute --git-common-dir)"

if [[ "$git_dir" != "$git_common_dir" ]]; then
  worktree_token="$(sanitize_worktree_token "$(basename "$PROJECT_ROOT")")"
  if [[ -n "$worktree_token" ]]; then
    export COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME:-food_registry}-${worktree_token}"
    export POSTGRES_DB="${POSTGRES_DB:-food_registry}_${worktree_token//-/_}"
    export POSTGRES_PORT="$(derive_worktree_postgres_port "$worktree_token")"
  fi
fi

if [[ -z "${APP_VERSION:-}" ]]; then
  APP_VERSION="$(git -C "$PROJECT_ROOT" describe --tags --always --dirty 2>/dev/null || true)"
  if [[ -n "$APP_VERSION" ]]; then
    export APP_VERSION
  fi
fi

cd "$PROJECT_ROOT"
docker compose "$@"
