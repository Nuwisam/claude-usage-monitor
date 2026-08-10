import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app.logging_config import configure
from app.routers import alert as alert_router
from app.routers import ingest as ingest_router
from app.routers import read as read_router
from app.routers import stream as stream_router

# The built frontend. Copied into the image from the `node` stage (see backend/Dockerfile);
# when running without a UI build the directory simply does not exist and the API works alone.
# STATIC_DIR allows pointing at a different directory — used by tests and by dev with a local `dist`.
STATIC_DIR = Path(
    os.environ.get("STATIC_DIR") or Path(__file__).resolve().parent.parent / "static"
)

configure()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("claude-usage-monitor backend start")
    yield
    logger.info("claude-usage-monitor backend stop")


# Schema and API browsers are disabled. The reverse proxy passes the whole container root
# through, so `/openapi.json` and `/docs` would be reachable WITHOUT authorization — the gate
# sits on the endpoint dependencies, not on those routes. They would then hand the full set of
# paths, field names and error shapes to anyone who guesses the address. docs/API.md describes
# the contract.
app = FastAPI(
    title="Claude Usage Monitor",
    version="0.1.0",
    lifespan=lifespan,
    openapi_url=None,
    docs_url=None,
    redoc_url=None,
)


@app.get("/api/health")
async def health() -> dict:
    """No authorization — this serves the container healthcheck, which has no session at all.

    It hands back the bare fact that the process is alive: no data, no configuration. The
    reverse proxy has no reason to expose it, but if it did, there is nothing here to carry off.
    """
    return {"status": "ok"}


@app.middleware("http")
async def no_store(request, call_next):
    """Limit data must not be cached anywhere along the way — showing a stale
    percentage is worse than no response at all."""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(alert_router.router, prefix="/api")
app.include_router(ingest_router.router, prefix="/api")
app.include_router(read_router.router, prefix="/api")
app.include_router(stream_router.router, prefix="/api")


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception):
    logger.exception("Unhandled exception: {}", exc)
    return JSONResponse(status_code=500, content={"reason": "internal-error"})


# --------------------------------------------------------------------------- UI
# Static files are served by the backend, with no separate nginx container. Reasons: the
# deployment host had exhausted Docker's address pools, and the nginx `auth_request` variant
# carries a known pitfall (`$scheme` inside the container is http, `$request_uri` comes without
# the prefix, so `rd=` after login throws the user at the service root). Here the SSO gate
# stays in the backend.
#
# ORDER MATTERS: the /api routers are mounted HIGHER UP, so a 404 from the API stays a 404
# from the API and does not come back as index.html.
if STATIC_DIR.is_dir():
    INDEX = STATIC_DIR / "index.html"

    # File names in /assets are hashed by Vite, so they can sit in cache for a long time.
    app.mount(
        "/assets",
        StaticFiles(directory=STATIC_DIR / "assets"),
        name="assets",
    )

    @app.middleware("http")
    async def static_cache(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.api_route("/{full_path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    async def spa(full_path: str):
        """SPA fallback. The HTML shell goes out WITHOUT SSO — deliberately.

        It carries no data, and the whole contract rests on the app receiving
        `401 + redirect_url` from /api/status and redirecting on its own. Gating
        index.html itself would hand back JSON instead of a page on the first visit.
        """
        # An unknown /api path MUST stay an API error. The routers are mounted higher up, but
        # it is this function that catches everything they did not match — without this
        # condition a typo in an endpoint address would hand back HTTP 200 and an HTML page
        # instead of a 404, and the client (or the probe) would try to parse that as JSON.
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail={"reason": "not-found"})

        # A file from the dist directory (favicon, robots) — if it exists, serve it directly.
        candidate = (STATIC_DIR / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(STATIC_DIR):
            return FileResponse(candidate)
        if not INDEX.is_file():
            raise HTTPException(status_code=404, detail={"reason": "ui-not-built"})
        return FileResponse(INDEX, headers={"Cache-Control": "no-store"})
