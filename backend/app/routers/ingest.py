"""POST /api/ingest — jedyny endpoint bez SSO.

Dwie warstwy auth: filtr brzegowy X-Ingest-Key w Apache (odcina skanery zanim dotkna
Pythona) i Bearer per maszyna tutaj (prawdziwa autoryzacja).

Cap rozmiaru jest sprawdzany PRZED parsowaniem JSON — endpoint jest wystawiony w internecie,
a klient moze wyslac backlog po dlugiej przerwie.
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_ingest_token
from app.config import settings
from app.db import get_session
from app.schemas import IngestResult
from app.services.events import publish_accounts
from app.services.ingest import ingest_one, utcnow

router = APIRouter()


@router.post("/ingest", response_model=IngestResult)
async def ingest(
    request: Request,
    machine: str = Depends(require_ingest_token),
    db: AsyncSession = Depends(get_session),
) -> IngestResult:
    raw = await request.body()
    if len(raw) > settings.max_ingest_body_bytes:
        logger.warning("Ingest odrzucony: body {} B > limit {} B",
                       len(raw), settings.max_ingest_body_bytes)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"reason": "body-too-large", "limit": settings.max_ingest_body_bytes},
        )
    try:
        import json
        payload = json.loads(raw or b"{}")
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail={"reason": "invalid-json"})
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail={"reason": "payload-not-object"})

    result = await ingest_one(db, machine_name=machine, payload=payload)
    touched: list[str] = [result.get("account_uuid")]

    # Backlog: partiami, z twardym capem. Klient obcina spool DOPIERO po potwierdzeniu,
    # ile wpisow przyjelismy — dzieki temu awaria w polowie nie gubi danych.
    backlog = payload.get("backlog") or []
    accepted = 0
    if isinstance(backlog, list):
        for entry in backlog[: settings.max_backlog_entries]:
            if not isinstance(entry, dict):
                continue
            try:
                r = await ingest_one(db, machine_name=machine, payload=entry,
                                     is_backlog=True)
                touched.append(r.get("account_uuid"))
                accepted += 1
            except Exception as exc:                       # noqa: BLE001
                logger.warning("Wpis z backlogu odrzucony: {}", exc)

    await db.commit()

    # SSE stream. Three things here are deliberate:
    #
    # 1. AFTER the commit. A receiver processes a frame within milliseconds and before the
    #    commit would see the pre-write state — then sit on it until the next poll.
    # 2. The gate is "batch assigned to an account", NOT `samples_written > 0`. On an
    #    unchanged value dedup writes no sample but does move `last_confirmed_at` — which
    #    is exactly what keeps the state `live`. Gating on the sample count would mute
    #    events in the most common case of all: when nothing is changing.
    # 3. An exception must NOT bring ingest down. The probe is the only code sitting in
    #    the path of your actual work; the chart may break, the session may not.
    try:
        await publish_accounts(db, [u for u in touched if u])
    except Exception as exc:                               # noqa: BLE001
        logger.warning("SSE publish failed: {}", exc)

    return IngestResult(
        ok=bool(result.get("ok")),
        samples_written=result.get("samples_written", 0),
        backlog_accepted=accepted,
        server_now=utcnow(),
        batch_id=result.get("batch_id"),
        series_registered=result.get("series_registered", []),
    )
