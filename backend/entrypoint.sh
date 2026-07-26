#!/bin/sh
set -e

echo "[entrypoint] Alembic: migracje..."
alembic upgrade head

echo "[entrypoint] Start uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --no-access-log
