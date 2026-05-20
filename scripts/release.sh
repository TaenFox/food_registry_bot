#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$PROJECT_ROOT/../food_registry_bot_local/.env"
COMPOSE_SCRIPT="$PROJECT_ROOT/scripts/docker-compose.sh"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Файл окружения не найден: $ENV_FILE" >&2
  echo "Создай его через ./scripts/init-dev-secrets.sh или подготовь вручную." >&2
  exit 1
fi

required_vars=(
  BOT_TOKEN
  ADMIN_USER_IDS
  OPENAI_API_KEY
)

for var_name in "${required_vars[@]}"; do
  if ! grep -Eq "^${var_name}=.+$" "$ENV_FILE"; then
    echo "В $ENV_FILE не задана обязательная переменная: $var_name" >&2
    exit 1
  fi
done

cd "$PROJECT_ROOT"

APP_VERSION="$(git describe --tags --exact-match 2>/dev/null || git rev-parse --short HEAD)"
export APP_VERSION

echo "Версия релиза: $APP_VERSION"

echo "Проверяю docker compose конфиг..."
"$COMPOSE_SCRIPT" config >/dev/null

echo "Запускаю релизный деплой..."
"$COMPOSE_SCRIPT" up --build -d

echo
echo "Текущее состояние контейнеров:"
"$COMPOSE_SCRIPT" ps

echo
echo "Последние логи бота:"
"$COMPOSE_SCRIPT" logs --tail=50 bot
