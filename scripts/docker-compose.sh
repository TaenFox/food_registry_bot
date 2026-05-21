#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/../food_registry_bot_local/.env"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Файл окружения не найден: $ENV_FILE" >&2
  echo "Создай его через ./scripts/init-dev-secrets.sh или подготовь вручную." >&2
  exit 1
fi

if [[ -z "${APP_VERSION:-}" ]]; then
  APP_VERSION="$(git -C "$PROJECT_ROOT" describe --tags --always --dirty 2>/dev/null || true)"
  if [[ -n "$APP_VERSION" ]]; then
    export APP_VERSION
  fi
fi

cd "$PROJECT_ROOT"
docker compose --env-file "$ENV_FILE" "$@"
