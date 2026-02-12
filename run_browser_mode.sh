#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON_BIN="${PYTHON_BIN:-python}"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
URL="http://${HOST}:${PORT}/"
USE_APP_DATA="${USE_APP_DATA:-1}"

cd "${ROOT_DIR}"

if [[ "${USE_APP_DATA}" == "1" ]]; then
  APP_ROOT="${HOME}/Library/Application Support/MyDjangoApp"
  DB_DIR="${APP_ROOT}/db"
  MEDIA_DIR="${APP_ROOT}/media"
  LOG_DIR="${APP_ROOT}/logs"
  BACKUP_DIR="${APP_ROOT}/backups"
  TEMP_DIR="${APP_ROOT}/temp"

  mkdir -p "${DB_DIR}" "${MEDIA_DIR}" "${LOG_DIR}" "${BACKUP_DIR}" "${TEMP_DIR}"

  export IOE_DB_PATH="${DB_DIR}/db.sqlite3"
  export IOE_MEDIA_ROOT="${MEDIA_DIR}"
  export IOE_LOG_DIR="${LOG_DIR}"
  export IOE_BACKUP_ROOT="${BACKUP_DIR}"
  export IOE_TEMP_DIR="${TEMP_DIR}"
  echo "[BROWSER-MODE] using app data root: ${APP_ROOT}"
fi

echo "[BROWSER-MODE] python manage.py migrate --noinput"
"${PYTHON_BIN}" manage.py migrate --noinput

echo "[BROWSER-MODE] starting Django at ${HOST}:${PORT}"
"${PYTHON_BIN}" manage.py runserver "${HOST}:${PORT}" --noreload &
SERVER_PID=$!

cleanup() {
  if kill -0 "${SERVER_PID}" >/dev/null 2>&1; then
    kill "${SERVER_PID}" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

for _ in {1..50}; do
  if ! kill -0 "${SERVER_PID}" >/dev/null 2>&1; then
    wait "${SERVER_PID}"
    exit 1
  fi
  sleep 0.1
done

if command -v open >/dev/null 2>&1; then
  echo "[BROWSER-MODE] opening ${URL}"
  open "${URL}"
else
  echo "[BROWSER-MODE] browser open command unavailable, please open: ${URL}"
fi

wait "${SERVER_PID}"
