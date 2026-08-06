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

# Zbudowany frontend. Kopiowany do obrazu z etapu `node` (patrz backend/Dockerfile);
# przy uruchomieniu bez buildu UI katalog po prostu nie istnieje i API dziala samo.
# STATIC_DIR pozwala wskazac inny katalog — uzywaja tego testy i dev z lokalnym `dist`.
STATIC_DIR = Path(
    os.environ.get("STATIC_DIR") or Path(__file__).resolve().parent.parent / "static"
)

configure()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("claude-usage-monitor backend start")
    yield
    logger.info("claude-usage-monitor backend stop")


# Schemat i przegladarki API wylaczone. Reverse proxy przepuszcza caly korzen kontenera,
# wiec `/openapi.json` i `/docs` bylyby osiagalne BEZ autoryzacji — brama siedzi na
# zaleznosci endpointow, nie na tych trasach. Oddawalyby wtedy komplet sciezek, nazw pol
# i ksztaltow bledow kazdemu, kto zgadnie adres. Kontrakt opisuje docs/API.md.
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
    """Bez autoryzacji — sluzy healthcheckowi kontenera, ktory nie ma zadnej sesji.

    Oddaje sam fakt, ze proces zyje: ani danych, ani konfiguracji. Reverse proxy nie ma
    powodu tego wystawiac, ale gdyby wystawil, nie ma tu czego wyniesc.
    """
    return {"status": "ok"}


@app.middleware("http")
async def no_store(request, call_next):
    """Dane limitow nie moga byc cache'owane po drodze — pokazanie nieaktualnego
    procentu jest gorsze niz brak odpowiedzi."""
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
    logger.exception("Nieobsluzony wyjatek: {}", exc)
    return JSONResponse(status_code=500, content={"reason": "internal-error"})


# --------------------------------------------------------------------------- UI
# Statyki serwuje backend, bez osobnego kontenera z nginx. Powody: host wyczerpal
# pule adresowe Dockera, a wariant z nginx `auth_request` niesie znana pulapke
# (`$scheme` w kontenerze to http, `$request_uri` jest bez prefiksu, wiec `rd=` po
# logowaniu wyrzuca na korzen serwisu). Tutaj brama SSO zostaje w backendzie.
#
# KOLEJNOSC MA ZNACZENIE: routery /api sa dopiete WYZEJ, wiec 404 z API zostaje 404
# z API i nie wraca jako index.html.
if STATIC_DIR.is_dir():
    INDEX = STATIC_DIR / "index.html"

    # Nazwy plikow w /assets sa hashowane przez Vite, wiec moga lezec w cache dlugo.
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
        """Fallback SPA. Powloka HTML idzie BEZ SSO — swiadomie.

        Nie ma w niej danych, a caly kontrakt stoi na tym, ze aplikacja dostaje
        `401 + redirect_url` z /api/status i sama przekierowuje. Bramkowanie samego
        index.html oddawaloby JSON zamiast strony przy pierwszym wejsciu.
        """
        # Nieznana sciezka /api MUSI zostac bledem API. Routery sa dopiete wyzej, ale
        # to ta funkcja lapie wszystko, czego nie dopasowaly — bez tego waruntku literowka
        # w adresie endpointu oddawalaby HTTP 200 i strone HTML zamiast 404, a klient
        # (albo sonda) probowalby to sparsowac jako JSON.
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail={"reason": "not-found"})

        # Plik z katalogu dist (favicon, robots) — jesli istnieje, oddaj go wprost.
        candidate = (STATIC_DIR / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(STATIC_DIR):
            return FileResponse(candidate)
        if not INDEX.is_file():
            raise HTTPException(status_code=404, detail={"reason": "ui-not-built"})
        return FileResponse(INDEX, headers={"Cache-Control": "no-store"})
