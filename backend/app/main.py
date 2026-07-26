from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from loguru import logger

from app.logging_config import configure
from app.routers import ingest as ingest_router
from app.routers import read as read_router

configure()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("claude-usage-monitor backend start")
    yield
    logger.info("claude-usage-monitor backend stop")


app = FastAPI(title="Claude Usage Monitor", version="0.1.0", lifespan=lifespan)


@app.get("/api/health")
async def health() -> dict:
    """Bez SSO — sluzy healthcheckowi kontenera. Apache tego nie wystawia."""
    return {"status": "ok"}


@app.middleware("http")
async def no_store(request, call_next):
    """Dane limitow nie moga byc cache'owane po drodze — pokazanie nieaktualnego
    procentu jest gorsze niz brak odpowiedzi."""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(ingest_router.router, prefix="/api")
app.include_router(read_router.router, prefix="/api")


@app.exception_handler(Exception)
async def unhandled(request, exc: Exception):
    logger.exception("Nieobsluzony wyjatek: {}", exc)
    return JSONResponse(status_code=500, content={"reason": "internal-error"})
