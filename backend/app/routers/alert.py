"""POST /api/session-alert — Claude Code sessions that have halted and await a human.

The second endpoint without SSO, after `/ingest`, and with the same pair of layers: the
edge filter `X-Ingest-Key` in Apache plus a per-machine Bearer here. The token identifies
the MACHINE, not the account — exactly as with ingest.

How this endpoint differs from `/ingest`, and why that is the right call:

  * It does NOT touch the database. No session, no transaction, no `_WRITE_LOCK` — the
    state goes into a dictionary in process memory. A block is momentary by definition:
    it dies the moment someone clicks "yes". A table would mean migrations and a row
    lifecycle for something that is meant to leave no trace behind.
  * It does not serialize writes. There is nothing to serialize: every POST REPLACES its
    machine's entry whole, so two overlapping requests from the same machine give the
    same result as the later one alone.
  * It publishes through `broker.publish_all`, not `publish` — an alert belongs to no
    single account, and the same frame has to reach the panel no matter which accounts
    the panel is subscribed to.

The size cap is checked BEFORE the JSON is parsed, the same as in `/ingest`: this endpoint
is exposed to the internet.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from pydantic import ValidationError

from app.auth import require_ingest_token
from app.config import settings
from app.schemas import SessionAlert, SessionAlertResult
from app.services.events import alert_frame, broker, set_alerts
from app.services.ingest import utcnow

router = APIRouter()

# The panel shows one card and a handful of "other:" rows. A set larger than that is a
# symptom of a sweep failure on the client side, not a state worth displaying.
MAX_ALERTS = 64


@router.post("/session-alert", response_model=SessionAlertResult)
async def session_alert(
    request: Request,
    machine: str = Depends(require_ingest_token),
) -> SessionAlertResult:
    raw = await request.body()
    if len(raw) > settings.max_ingest_body_bytes:
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

    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail={"reason": "entries-not-list"})

    alerts: list[SessionAlert] = []
    for item in entries[:MAX_ALERTS]:
        if not isinstance(item, dict):
            continue
        try:
            # `machine` is set by the SERVER from the token, never by the client: the
            # machine name is the label shown on the panel, and the only place where
            # someone could impersonate another's entry if it were allowed to be sent.
            alerts.append(SessionAlert(**dict(item, machine=machine)))
        except ValidationError:
            # A single broken entry must not wipe the machine's whole set — that would
            # mean a formatting error silencing the alert.
            logger.warning("session-alert: machine {} sent an unacceptable entry",
                           machine)

    set_alerts(machine, alerts)
    reached = broker.publish_all(alert_frame(now=utcnow()))
    logger.info("session-alert: {} — {} entry(s), {} subscriber(s)",
                machine, len(alerts), reached)
    return SessionAlertResult(ok=True, machine=machine, accepted=len(alerts),
                              subscribers=reached)
