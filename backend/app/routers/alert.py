"""POST /api/session-alert — sesje Claude Code, ktore stanely i czekaja na czlowieka.

Drugi endpoint bez SSO, po `/ingest`, i z ta sama para warstw: filtr brzegowy
`X-Ingest-Key` w Apache oraz Bearer per maszyna tutaj. Token identyfikuje MASZYNE,
a nie konto — dokladnie jak przy ingescie.

Czym ten endpoint rozni sie od `/ingest`, i dlaczego to jest wlasciwe:

  * NIE dotyka bazy. Ani sesji, ani transakcji, ani `_WRITE_LOCK` — stan trafia do
    slownika w pamieci procesu. Blokada jest z definicji chwilowa: gasnie, gdy ktos
    kliknie "tak". Tabela oznaczalaby migracje i cykl zycia wierszy dla czegos, po
    czym nie ma sie zostac zaden slad.
  * Nie serializuje zapisow. Nie ma czego serializowac: kazdy POST ZASTEPUJE wpis
    swojej maszyny w calosci, wiec dwa nakladajace sie zadania z tej samej maszyny
    daja ten sam wynik co jedno pozniejsze.
  * Publikuje przez `broker.publish_all`, nie `publish` — alert nie nalezy do zadnego
    pojedynczego konta, a ta sama ramka ma dojsc do panelu niezaleznie od tego, na
    ktore konta jest zapisany.

Ciezar rozmiaru sprawdzamy PRZED parsowaniem JSON, tak samo jak w `/ingest`: endpoint
jest wystawiony w internecie.
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

# Panel pokazuje jedna karte i garstke wierszy "inne:". Zbior wiekszy niz to jest
# objawem awarii zamiatania po stronie klienta, nie stanem do wyswietlenia.
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
            # `machine` nadaje SERWER z tokenu, nigdy klient: nazwa maszyny jest
            # etykieta widoczna na panelu i jedynym miejscem, w ktorym ktos moglby
            # sie podszyc pod cudzy wpis, gdyby wolno ja bylo przyslac.
            alerts.append(SessionAlert(**dict(item, machine=machine)))
        except ValidationError:
            # Pojedynczy zepsuty wpis nie moze skasowac calego zbioru maszyny —
            # to byloby zgaszenie alertu przez blad formatowania.
            logger.warning("session-alert: maszyna {} przyslala wpis nie do przyjecia",
                           machine)

    set_alerts(machine, alerts)
    reached = broker.publish_all(alert_frame(now=utcnow()))
    logger.info("session-alert: {} — {} wpis(ow), {} odbiorca(ow)",
                machine, len(alerts), reached)
    return SessionAlertResult(ok=True, machine=machine, accepted=len(alerts),
                              subscribers=reached)
