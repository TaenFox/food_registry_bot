#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_NAME="$(basename "${PROJECT_ROOT}")"
SECRETS_DIR="${PROJECT_ROOT}/../${PROJECT_NAME}_local"
ENV_FILE="${SECRETS_DIR}/.env"
README_FILE="${SECRETS_DIR}/README.md"

mkdir -p "${SECRETS_DIR}"

if [[ ! -f "${ENV_FILE}" ]]; then
  cat > "${ENV_FILE}" <<'EOF'
APP_ENV=local
BOT_TOKEN=
ADMIN_USER_IDS=
EXTRACTION_PROVIDER=structured_payload
LLM_MODEL=gpt-5-mini
OPENAI_API_KEY=

POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=food_registry
POSTGRES_USER=food_registry
POSTGRES_PASSWORD=food_registry
EOF
fi

cat > "${README_FILE}" <<EOF
# ${PROJECT_NAME}_local

Этот каталог хранит локальные секреты и конфигурацию окружения для проекта \`${PROJECT_NAME}\`.

- Основной файл: \`.env\`
- Каталог расположен рядом с репозиторием и не должен коммититься в git этого проекта
- Скрипты и код проекта ожидают секреты по пути \`../${PROJECT_NAME}_local/.env\` относительно корня репозитория
- Для admin-команд укажи список Telegram user id в \`ADMIN_USER_IDS\` через запятую
- Для direct LLM extraction укажи \`EXTRACTION_PROVIDER=llm\` и заполни \`OPENAI_API_KEY\`
EOF

echo "Secrets template is ready: ${ENV_FILE}"
