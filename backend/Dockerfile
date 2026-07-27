# Kontekst budowania to KATALOG REPO (nie ./backend) — etap `ui` musi widziec frontend/.
# Patrz docker-compose.yml: build.context = "." , build.dockerfile = "backend/Dockerfile".

# --------------------------------------------------------------------------- UI
FROM node:22-alpine AS ui

# Publiczna sciezka montowania. Musi zgadzac sie z APP_BASE_PATH backendu i z regula
# ProxyPass w Apache — jedna wartosc, trzech konsumentow.
ARG VITE_BASE_PATH=/claude-usage/
ENV VITE_BASE_PATH=${VITE_BASE_PATH}

WORKDIR /ui

COPY frontend/package.json frontend/package-lock.json* ./
# NIE dawaj --omit=optional: rollup wozi natywne binaria (np. @rollup/rollup-linux-x64-musl)
# jako optionalDependencies, a bez nich build sie nie uruchomi.
# Dlugie timeouty sa konieczne — z tej sieci npm regularnie zrywa na pierwszym podejsciu.
RUN npm install --no-audit --no-fund --fetch-timeout=600000 --fetch-retries=5

COPY frontend/ ./
RUN npm run build

# ------------------------------------------------------------------------ backend
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY backend/pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
COPY backend/entrypoint.sh ./

# Statyki UI. Backend montuje /assets i oddaje index.html jako fallback SPA (app/main.py).
COPY --from=ui /ui/dist ./static

RUN chmod +x entrypoint.sh

EXPOSE 8000

CMD ["./entrypoint.sh"]
