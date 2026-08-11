"""GET /api/stream — SSE carrying account cards the client subscribed to.

Subscription is BY `account_uuid` ONLY. The email does not appear in this contract at all,
neither as a parameter nor as a matching key: one email address really does point at several
accounts, and `Account.email` is overwritten on every ingest — addressing by it would mean
the set of accounts under a subscription changes without the subscriber knowing (rule 7).

The endpoint does NOT hold a database session for the lifetime of the connection. A session
is opened to resolve the accounts and emit the snapshot, then closed; the long loop never
touches the database, because finished frames are delivered by the ingest publisher.
Without this, every open stream would hold a pooled connection (5 + 10 overflow by default)
for the full 15 minutes and a dozen clients would stall the entire backend.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.auth import require_stream_client
from app.config import settings
from app.db import SessionLocal
from app.models import Account
from app.services.events import (
    account_frame, alert_frame, broker, bye_frame, hello_frame, ping_frame,
)
from app.services.ingest import utcnow

router = APIRouter()


@router.get("/stream")
async def stream(
    request: Request,
    account: list[str] = Query(default=[], description="account_uuid; repeatable"),
    snapshot: bool = Query(True, description="0 = skip the initial cards"),
    client: str = Depends(require_stream_client),
) -> StreamingResponse:
    wanted = list(dict.fromkeys(a.strip() for a in account if a.strip()))
    if not wanted:
        # A missing parameter must NOT mean "everything" — subscribing to every account by
        # oversight is exactly what this endpoint is built not to do.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail={"reason": "no-subscription"})
    if broker.count >= settings.stream_max_clients:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail={"reason": "too-many-streams"})

    # Subscribe to ALL requested UUIDs, including unknown ones. An account with that UUID
    # may only appear later (the first `/login` onto it), and then the publisher will find
    # a listener without any extra machinery.
    sub = broker.subscribe(frozenset(wanted), client)

    async def gen():
        try:
            # Clients should reconnect quickly. The browser's default 3 s would land here
            # anyway, but a headless panel reads this off the wire and need not assume.
            yield "retry: 3000\n\n"

            async with SessionLocal() as db:
                rows = (await db.execute(
                    select(Account).where(Account.account_uuid.in_(wanted))
                )).scalars().all()
                by_uuid = {a.account_uuid: a for a in rows}
                yield hello_frame(
                    subscribed=[u for u in wanted if u in by_uuid],
                    unknown=[u for u in wanted if u not in by_uuid],
                    now=utcnow(),
                )
                if snapshot:
                    for u in wanted:
                        acc = by_uuid.get(u)
                        if acc is not None:
                            yield await account_frame(db, acc, now=utcnow())
                    # Anything queued while we were building the snapshot is OLDER than
                    # it, and the receiver applies frames in order — delivering those
                    # would roll its view backwards. The snapshot supersedes them.
                    while not sub.queue.empty():
                        sub.queue.get_nowait()

            # Alerts AFTER the queue is cleared, never before — otherwise the loop
            # above would eat the snapshot just built. For `account` frames that was
            # harmless by design (the snapshot replaces them); for an ephemeral alert
            # it would be silent and fatal.
            #
            # Replaying the state on EVERY connection is a requirement, not a
            # decoration: STREAM_MAX_LIFETIME_SEC forces the panel to swap the
            # connection every 15 minutes, and a blocked session emits no event at
            # all in that time (measured: 98% of blocks). Without this, a block
            # lasting 40 minutes would vanish from the screen after fifteen.
            if snapshot:
                yield alert_frame(now=utcnow())

            # From here the loop never touches the database: frames arrive ready-made,
            # built by the publisher on the ingest session.
            started = utcnow()

            def remaining() -> float:
                return (settings.stream_max_lifetime_sec
                        - (utcnow() - started).total_seconds())

            while True:
                if remaining() <= 0:
                    # The only point at which a long connection re-verifies its SSO
                    # session. EventSource reconnects by itself, so the browser never
                    # notices.
                    yield bye_frame("lifetime")
                    return
                try:
                    # min() with the remaining lifetime, not the ping interval alone:
                    # otherwise the lifetime would overshoot by a full ping interval,
                    # because the check above only runs after the wait returns.
                    yield await asyncio.wait_for(
                        sub.queue.get(),
                        timeout=min(settings.stream_ping_sec, remaining()))
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        return
                    # If the wait ended because the lifetime ran out rather than the ping
                    # interval, skip the ping — `bye` follows immediately anyway.
                    if remaining() > 0:
                        yield ping_frame(utcnow())
        finally:
            broker.unsubscribe(sub)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Connection": "keep-alive",
            # An nginx-ism; Apache sits in front of us here, but the header is free and
            # guards against the proxy quietly changing later.
            "X-Accel-Buffering": "no",
        },
    )
