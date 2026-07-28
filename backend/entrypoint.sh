#!/bin/sh
set -e

echo "[entrypoint] Alembic: migracje..."
alembic upgrade head

echo "[entrypoint] Start uvicorn..."
# JEDEN proces, swiadomie. Broker SSE (app/services/events.py) trzyma subskrypcje w pamieci,
# wiec przy --workers > 1 ingest trafialby do innego procesu niz polaczenie klienta i czesc
# subskrybentow przestalaby dostawac ramki BEZ zadnego bledu w logach. Skalowanie w poziom
# wymaga najpierw brokera poza procesem (Redis pub/sub albo LISTEN/NOTIFY).
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --no-access-log
