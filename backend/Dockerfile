# The build context is the REPO DIRECTORY (not ./backend) — the `ui` stage must see frontend/.
# See docker-compose.yml: build.context = "." , build.dockerfile = "backend/Dockerfile".

# --------------------------------------------------------------------------- UI
FROM node:22-alpine AS ui

# The public mount path. It must agree with the backend's APP_BASE_PATH and with the
# ProxyPass rule in Apache — one value, three consumers.
ARG VITE_BASE_PATH=/claude-usage/
ENV VITE_BASE_PATH=${VITE_BASE_PATH}

WORKDIR /ui

COPY frontend/package.json frontend/package-lock.json* ./
# Do NOT add --omit=optional: rollup carries native binaries (e.g. @rollup/rollup-linux-x64-musl)
# as optionalDependencies, and without them the build will not run.
# Long timeouts are necessary — npm regularly drops on the first attempt from some networks.
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

# UI statics. The backend mounts /assets and serves index.html as the SPA fallback (app/main.py).
COPY --from=ui /ui/dist ./static

RUN chmod +x entrypoint.sh

EXPOSE 8000

CMD ["./entrypoint.sh"]
