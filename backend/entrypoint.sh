#!/bin/sh
set -e

echo "[entrypoint] Alembic: running migrations..."
alembic upgrade head

echo "[entrypoint] Starting uvicorn..."
# ONE process, deliberately. The SSE broker (app/services/events.py) keeps subscriptions in
# memory, so with --workers > 1 an ingest would land in a different process than the client
# connection and some subscribers would stop receiving frames WITHOUT any error in the logs.
# Scaling out first requires a broker outside the process (Redis pub/sub or LISTEN/NOTIFY).
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --no-access-log
