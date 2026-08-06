"""SSE broker — pushes account cards to clients subscribed to specific `account_uuid`s.

Three decisions that are not obvious:

1. A FRAME CARRIES THE FULL CARD, not a bare "something changed" signal. A thin signal
   would force the receiver to go fetch from /api/status, and that endpoint is SSO-only —
   so the desk panel would require opening the read gate to a token. The fat event keeps
   that surface closed and is cheaper anyway: one frame instead of a full multi-account
   response plus, under AUTH_MODE=verify, a round-trip to the identity service.

2. THE KEY IS `account_uuid`, NEVER THE EMAIL. One address really does point at several
   accounts (Pro and a Team seat under the same address), and `Account.email` is
   overwritten on every ingest from the payload. Addressing by email would mean the set of
   accounts under a subscription changes without the subscriber knowing — silently.
   `account_uuid` is the account's natural key and the only identifier that does not do
   this (rule 7).

3. PUBLISHING NEVER WAITS. `put_nowait` only; on a full queue we drain it and insert a
   `lag` frame. Ingest sits in the path of your actual work — a slow consumer must not
   slow the probe down, and losing intermediate frames is harmless because every frame is
   the account's FULL state, not a delta.

The broker lives IN-PROCESS. That works because uvicorn starts without `--workers` (see
entrypoint.sh); with several workers an ingest would land in a different process than the
client's connection and some subscribers would go silent without a trace in the logs.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Account
from app.schemas import AccountFrame, AlertFrame, HelloFrame, PingFrame, SessionAlert
from app.services.ingest import utcnow
from app.services.status import (
    CONTRACT_VERSION, build_account_status, last_batch_time,
)


# --------------------------------------------------------------------------- wire format
def sse(event: str, data: str) -> str:
    """One SSE block. `data` must already be serialized JSON.

    Newlines inside the data are split across successive `data:` fields — otherwise a
    single `\\n` in the content would terminate the event halfway and the receiver would
    get truncated JSON. Our payloads are single-line, but that is an assumption about our
    own code, not about the data.
    """
    body = "".join("data: %s\n" % line for line in data.split("\n"))
    return "event: %s\n%s\n" % (event, body)


def hello_frame(*, subscribed: list[str], unknown: list[str], now: datetime) -> str:
    return sse("hello", HelloFrame(
        contract_version=CONTRACT_VERSION, server_now=now,
        subscribed=subscribed, unknown=unknown,
        ping_sec=settings.stream_ping_sec,
        max_lifetime_sec=settings.stream_max_lifetime_sec,
    ).model_dump_json(by_alias=True))


def ping_frame(now: datetime) -> str:
    return sse("ping", PingFrame(server_now=now).model_dump_json(by_alias=True))


def lag_frame(dropped: int) -> str:
    return sse("lag", '{"reason":"queue-overflow","dropped":%d}' % dropped)


def bye_frame(reason: str) -> str:
    return sse("bye", '{"reason":"%s"}' % reason)


# --------------------------------------------------------------------------- alerts
# Sesje, ktore stanely i czekaja na czlowieka. IN-PROCESS, bez bazy — stan z definicji
# chwilowy, a tabela oznaczalaby migracje i cykl zycia wierszy dla czegos, co gasnie,
# gdy ktos kliknie "tak". Restart backendu czysci mape swiadomie: alerty wracaja przy
# najblizszym zdarzeniu z maszyny.
#
# Klucz to NAZWA MASZYNY z tokenu ingestu, a wartoscia jest CALY biezacy zbior tej
# maszyny. Kazdy POST zastepuje wpis maszyny w calosci, wiec nie ma tu stanu do
# uzgadniania i nie ma sposobu, zeby zgubione "wyjscie" zostawilo sierote.
ALERTS: dict[str, list[SessionAlert]] = {}

# Sufit wieku przy skladaniu snapshotu. Writer ma wlasny TTL (`blocked_ttl_sec`), ale
# maszyna, ktora zniknela w trakcie blokady, nigdy juz nie przysle korekty — bez tego
# jej wpis wisialby do restartu procesu.
ALERT_MAX_AGE_SEC = 86400.0


def set_alerts(machine: str, alerts: list[SessionAlert]) -> None:
    if alerts:
        ALERTS[machine] = alerts
    else:
        ALERTS.pop(machine, None)


def _aware(v: datetime) -> datetime:
    return v if v.tzinfo is not None else v.replace(tzinfo=timezone.utc)


def current_alerts(*, now: datetime) -> list[SessionAlert]:
    """Zbior ze wszystkich maszyn, najstarsze pierwsze. Wpis bez `since` przechodzi:
    brak stempla znaczy 'nie wiem, jak dlugo', nie 'przeterminowany' — i laduje na
    koncu, bo o kolejnosci ma decydowac wiek, a nie brak wiedzy o nim."""
    ref = _aware(now)
    out: list[SessionAlert] = []
    for alerts in ALERTS.values():
        for a in alerts:
            if a.since is not None and (ref - _aware(a.since)).total_seconds() > ALERT_MAX_AGE_SEC:
                continue
            out.append(a)
    out.sort(key=lambda a: (a.since is None, _aware(a.since) if a.since else ref))
    return out


def alert_frame(*, now: datetime) -> str:
    return sse("alert", AlertFrame(
        contract_version=CONTRACT_VERSION, server_now=now,
        alerts=current_alerts(now=now),
    ).model_dump_json(by_alias=True))


# --------------------------------------------------------------------------- broker
@dataclass(eq=False)      # eq=False => hashable by identity, so it can live in a set
class Subscriber:
    uuids: frozenset[str]
    label: str
    queue: asyncio.Queue[str] = field(default_factory=lambda: asyncio.Queue(
        maxsize=settings.stream_queue_max))


def _drain(q: asyncio.Queue[str]) -> int:
    n = 0
    while True:
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            return n
        n += 1


class Broker:
    def __init__(self) -> None:
        self._subs: set[Subscriber] = set()
        # Reverse index uuid -> subscribers. Without it every ingest would scan the
        # connection list; with it `has_subscribers` and `publish` are O(1) in the number
        # of clients.
        self._by_uuid: dict[str, set[Subscriber]] = {}

    @property
    def count(self) -> int:
        return len(self._subs)

    def subscribe(self, uuids: frozenset[str], label: str) -> Subscriber:
        sub = Subscriber(uuids=uuids, label=label)
        self._subs.add(sub)
        for u in uuids:
            self._by_uuid.setdefault(u, set()).add(sub)
        logger.info("SSE: client {} subscribed to {} account(s); {} total",
                    label, len(uuids), len(self._subs))
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)
        for u in sub.uuids:
            rest = self._by_uuid.get(u)
            if rest is None:
                continue
            rest.discard(sub)
            if not rest:
                del self._by_uuid[u]
        logger.info("SSE: client {} disconnected; {} total", sub.label, len(self._subs))

    def has_subscribers(self, account_uuid: str) -> bool:
        """Lets the publisher skip building a frame entirely when nobody is listening —
        with the browser closed, ingest pays nothing for the stream."""
        return bool(self._by_uuid.get(account_uuid))

    def publish(self, account_uuid: str, frame: str) -> int:
        """Returns how many receivers the frame reached.

        Deliberately synchronous: the absence of any `await` guarantees no other task can
        slip between iterations and mutate the set we are walking.
        """
        subs = self._by_uuid.get(account_uuid)
        if not subs:
            return 0
        sent = 0
        for sub in subs:
            try:
                sub.queue.put_nowait(frame)
                sent += 1
            except asyncio.QueueFull:
                # The receiver cannot keep up. Intermediate frames are disposable because
                # each one carries the account's FULL state — we keep only the `lag`
                # signal, and the next frame will be complete anyway.
                dropped = _drain(sub.queue)
                sub.queue.put_nowait(lag_frame(dropped))
                logger.warning("SSE: client {} fell behind, dropped {} frame(s)",
                               sub.label, dropped)
        return sent

    def publish_all(self, frame: str) -> int:
        """A frame that belongs to no single account — today: session alerts.

        Deliberately NOT a second subscription axis. The alternative (index by machine,
        subscribe the panel to machines) was designed and dropped: it rested on the
        premise that routing by account would leak project names to OTHER subscribers,
        and that premise is false here. `ALLOWED_EMAILS` holds one address, sso.py
        rejects every other one with 403 even if the proxy lets it through, and
        `STREAM_TOKENS` holds a single entry labelled `panel`. There is no second
        person for anything to leak to.

        The frame carries `machine` per entry, so a filter can be added LATER without
        changing the wire format, the day a second stream token appears.
        """
        sent = 0
        for sub in self._subs:
            try:
                sub.queue.put_nowait(frame)
                sent += 1
            except asyncio.QueueFull:
                dropped = _drain(sub.queue)
                sub.queue.put_nowait(lag_frame(dropped))
                logger.warning("SSE: client {} fell behind, dropped {} frame(s)",
                               sub.label, dropped)
        return sent


broker = Broker()


# --------------------------------------------------------------------------- publishing
async def account_frame(db: AsyncSession, account: Account, *, now: datetime) -> str:
    """An account card wrapped in a frame envelope. Built by `build_account_status` — the
    same function /api/status uses, so two different cards for one account cannot exist."""
    card, warnings = await build_account_status(
        db, account, now=now, last_batch_at=await last_batch_time(db, account.id),
    )
    return sse("account", AccountFrame(
        contract_version=CONTRACT_VERSION, server_now=now,
        account=card, warnings=warnings,
    ).model_dump_json(by_alias=True))


async def publish_accounts(db: AsyncSession, account_uuids: list[str]) -> int:
    """Sends one frame per account. Called AFTER `db.commit()` — never before.

    Publishing before the commit is a real race: the receiver processes a frame within
    milliseconds and would see the pre-write state, then sit on it until the next poll.

    `dict.fromkeys` removes duplicates while preserving order — a batch with a backlog can
    carry a dozen entries for the same account, and that is one event, not a dozen.
    """
    now = utcnow()
    sent = 0
    for uuid in dict.fromkeys(account_uuids):
        if not uuid or not broker.has_subscribers(uuid):
            continue
        account = (await db.execute(
            select(Account).where(Account.account_uuid == uuid)
        )).scalar_one_or_none()
        if account is None:
            continue
        sent += broker.publish(uuid, await account_frame(db, account, now=now))
    return sent
