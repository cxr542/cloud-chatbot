#!/bin/sh
set -eu

export PYTHONUNBUFFERED=1
export UVICORN_HOST=0.0.0.0

PORT="${PORT:-10000}"

if [ ! -f /app/.env ] && [ -n "${LLM_API_KEY:-}" ]; then
  cat >/app/.env <<EOF
ADMIN_USERNAME=${ADMIN_USERNAME:-admin}
ADMIN_PASSWORD=${ADMIN_PASSWORD:-change-me}
DB_PATH=chatbot_data.db
LLM_API_KEY=${LLM_API_KEY}
EOF
fi

python -m uvicorn backend.main:app --host 0.0.0.0 --port 9999 &
python -m uvicorn admin.main:app --host 0.0.0.0 --port 9998 &

for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if wget -q -O /dev/null http://127.0.0.1:9999/ 2>/dev/null; then
    break
  fi
  sleep 1
done

export PORT
envsubst '${PORT}' </app/deploy/nginx.conf.template >/tmp/nginx.conf
exec nginx -c /tmp/nginx.conf -g 'daemon off;'
