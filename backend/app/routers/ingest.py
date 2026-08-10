"""POST /api/ingest — the only endpoint without SSO.

Two auth layers: the edge filter X-Ingest-Key in Apache (cuts scanners off before they
reach Python) and a per-machine Bearer here (the real authorization).

The size cap is checked BEFORE the JSON is parsed — the endpoint is exposed to the internet,
and a client may send a backlog after a long break.

Writes are SERIALIZED and every measurement gets its OWN transaction — see `_WRITE_LOCK`.
"""
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_ingest_token
from app.config import settings
from app.db import get_session
from app.schemas import IngestResult
from app.services.events import publish_accounts
from app.services.ingest import ingest_one, request_offset, utcnow

router = APIRouter()

# Serialization of ingest WRITES within the process.
#
# `ingest_one` starts with a SELECT on `machines` (services/ingest.py:61) — that opens the
# transaction's read view — and then mutates that same, SINGLE row (`last_seen_at`,
# `batches`) and inserts a child into `ingest_batches` with an FK onto it, so the insert
# must take a lock on the parent row. MariaDB since 11.6.2 ships
# innodb_snapshot_isolation=ON, and then an attempt to lock a row changed AFTER the read
# view opened does not wait — it returns a hard ER_CHECKREAD (1020). Two overlapping POSTs
# from the same machine are the norm here (parallel subagent session hooks): that gave
# 36x 1020 and 9x deadlock 1213, and each of them rolled back the WHOLE request — batch,
# raw_payload, samples, events and the appended backlog.
#
# The lock covers the WHOLE span "read view open -> commit". Shortening it to the UPDATE
# alone buys nothing: the read view is created at the first SELECT already. Verified that
# the span really starts under the lock — `require_ingest_token` (app/auth.py:37) does not
# touch the database, and `AsyncSession` opens no transaction before the first `execute()`.
#
# The hot rows are not only `machines`: the same select-then-mutate shape appears in
# `get_or_create_account` (services/ingest.py:73), `get_or_create_series` (:100),
# `store_raw` (:134) and the `MachineAccount` pair upsert in `ingest_one`. `raw_payloads`
# is hot PRECISELY when nothing is happening, because the response is then byte-identical
# and falls into the same row — one shared by all machines and all accounts. Hence a single
# lock, covering the whole of `ingest_one` rather than just the machine part.
#
# WHY AN IN-PROCESS LOCK IS ENOUGH: entrypoint.sh starts a single uvicorn process
# DELIBERATELY, because the SSE broker (services/events.py) lives in process memory — see
# AGENTS.md, "Pitfalls of this environment". With --workers > 1 this lock falls silent and
# 1020 comes back. It is the same condition AGENTS.md already forbids breaking, but there
# it breaks loudly (SSE stops arriving) and here QUIETLY, so it is spelled out.
#
# This does NOT replace a retry on 1020/1213 (F4, deliberately out of scope): the lock
# removes the source of collisions INSIDE the process, not resilience to outside ones.
_WRITE_LOCK = asyncio.Lock()


@asynccontextmanager
async def _ingest_tx(db: AsyncSession) -> AsyncIterator[None]:
    """One measurement = one transaction, wholly under the lock, with commit AND rollback
    INSIDE. The rollback must hold the lock: until it returns the session is poisoned and
    the next waiter would get a PendingRollbackError instead of a chance to write."""
    async with _WRITE_LOCK:
        try:
            yield
            await db.commit()
            # `commit()` does NOT expire objects: SessionLocal has expire_on_commit=False
            # (app/db.py:14). That setting is tuned for ONE transaction per request — after
            # the commit nobody read from that session again. Here there are N transactions
            # and the lock is RELEASED between them, so another request commits in the gap.
            # Without this line the next `select()` would take THE SAME object from the
            # identity map, with values from before that other commit (SQLAlchemy returns
            # the instance from the identity map and discards the row's fresh columns), and
            # would quietly overwrite someone's increment to `machines.batches`,
            # `raw_payloads.seen_count`, `machine_accounts.samples`, while the monotonicity
            # guard would compare against a stale `series_state`.
            #
            # This line is literally what the default expire_on_commit=True does — restoring
            # the SQLAlchemy default for exactly the commits this split introduces, without
            # changing it for the rest of the application. So it is not a defense against a
            # hypothesis: it removes the data-consistency path's dependency on whether
            # refcounting managed to evict the object from the weak identity map — a
            # condition written down nowhere, guarded by no test, and breaking QUIETLY.
            #
            # The rollback path needs no equivalent: `Session.rollback()` expires everything
            # itself, always.
            db.expire_all()
        except BaseException:
            # BaseException, not Exception: CancelledError does NOT inherit from Exception
            # since 3.8, and it really does arrive here — Starlette cancels the request when
            # the client disconnects (and the probe has a 5 s timeout), uvicorn cancels
            # in-flight requests on SIGTERM. Letting it through with an open transaction
            # would hold the lock on the `machines` row until the pooled connection dies.
            await db.rollback()
            raise


@router.post("/ingest", response_model=IngestResult)
async def ingest(
    request: Request,
    machine: str = Depends(require_ingest_token),
    db: AsyncSession = Depends(get_session),
) -> IngestResult:
    # TIME ANCHOR — the first statement, before the body is read and before `_ingest_tx`.
    # `ingest_one` already runs under `_WRITE_LOCK`; taking the anchor there would mean a
    # request that waited out someone else's backlog dates its measurements too fresh by
    # the wait, and each backlog entry gets its own, different anchor. One per whole request.
    arrived_at = utcnow()

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

    # `offset = arrived_at - sent_at` — one per whole request, computed from the OUTER
    # record, because that is the one just sent. Backlog entries ride in the same envelope,
    # so their age (`sent_at - ts` on the client's OWN clock) is corrected by the same
    # amount. No `sent_at` => a zero offset, so the client's stamp goes through untouched.
    offset = request_offset(payload, arrived_at)

    async with _ingest_tx(db):
        result = await ingest_one(db, machine_name=machine, payload=payload,
                                  arrived_at=arrived_at, offset=offset)
    touched: list[str] = [result.get("account_uuid")]

    # Backlog: in batches, with a hard cap. The client trims its spool ONLY after being
    # told how many entries were accepted — so a failure halfway loses no data.
    #
    # Every entry gets its OWN transaction, because for the probe `backlogAccepted` is the
    # PREFIX LENGTH to trim the spool by (`trim_spool` does `lines[accepted:]`), not a count
    # of measurements written. One shared transaction would mean an error on entry k rolling
    # back entries 0..k-1 that were already counted — and the probe would drop them.
    backlog = payload.get("backlog") or []
    accepted = 0
    if isinstance(backlog, list):
        for entry in backlog[: settings.max_backlog_entries]:
            if not isinstance(entry, dict):
                # Counted, even though not written. `accepted` is a POSITION, not a
                # success meter: skipping it without an increment shifts the numbering
                # of every later entry and the probe trims the wrong piece of the
                # spool — losing data, or sending it twice. Such an entry can never be
                # written, so owning up to it is correct.
                accepted += 1
                continue
            try:
                async with _ingest_tx(db):
                    r = await ingest_one(db, machine_name=machine, payload=entry,
                                         arrived_at=arrived_at, offset=offset,
                                         is_backlog=True)
            except Exception as exc:                       # noqa: BLE001
                # `break`, not `continue`: `accepted` is a prefix, so counting an entry
                # that was NOT written would tell the probe to drop it from the spool.
                # Stop at the first unwritten one — the tail returns next request.
                #
                # A KNOWN COST, taken deliberately: an entry that can NEVER be written
                # (permanently bad shape) stalls the tail for good. The raw content is
                # in the database and in the log, but the decision "what to do with
                # such an entry" — quarantine after N tries, or per-entry results on
                # the wire — moves the schema or contract v3, and is the user's to
                # make, not this loop's.
                #
                # Exception, and NOT BaseException: CancelledError must fly on and
                # abort the request instead of looking like one bad entry.
                logger.warning("Backlog: wpis {} odrzucony, ogon zostaje w spoolu: {}",
                               accepted, exc)
                break
            # Only AFTER leaving `_ingest_tx`, that is after a successful commit —
            # otherwise entries a rollback had just undone would be counted.
            touched.append(r.get("account_uuid"))
            accepted += 1

    # SSE stream. Three things here are deliberate:
    #
    # 1. AFTER the last commit. A receiver processes a frame within milliseconds and before
    #    the commit would see the pre-write state — then sit on it until the next poll.
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
