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

    # Backlog: partiami, z twardym capem. Klient obcina spool DOPIERO po potwierdzeniu,
    # ile wpisow przyjelismy — dzieki temu awaria w polowie nie gubi danych.
    backlog = payload.get("backlog") or []
    accepted = 0
    if isinstance(backlog, list):
        for entry in backlog[: settings.max_backlog_entries]:
            if not isinstance(entry, dict):
                continue
            try:
                await ingest_one(db, machine_name=machine, payload=entry, is_backlog=True)
                accepted += 1
            except Exception as exc:                       # noqa: BLE001
                logger.warning("Wpis z backlogu odrzucony: {}", exc)

    await db.commit()
    return IngestResult(
        ok=bool(result.get("ok")),
        samples_written=result.get("samples_written", 0),
        backlog_accepted=accepted,
        server_now=utcnow(),
        batch_id=result.get("batch_id"),
        series_registered=result.get("series_registered", []),
    )
