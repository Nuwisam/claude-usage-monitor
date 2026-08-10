"""Backlog, transaction boundaries and idempotence — what can be checked without MariaDB.

WHAT THIS FILE DELIBERATELY AND EXPLICITLY DOES NOT COVER:
  * error 1020 itself — it is specific to MariaDB with innodb_snapshot_isolation=ON,
    and the suite runs on SQLite;
  * REAL concurrency — every test below is SEQUENTIAL. The last one shows the EFFECT
    of an interleaving (someone else's commit in the gap), not the interleaving itself;
  * a response lost on the probe's side — that is on the other side of HTTP.

Mind the contract: `IngestResult` is a `CamelModel` and FastAPI serializes by alias, so
on the wire it is `backlogAccepted`, not `backlog_accepted`. The probe reads the same
(client/usage-probe.py, `parsed.get("backlogAccepted")`).
"""
import asyncio
from datetime import timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base, IngestBatch, LimitSample, Machine
from app.routers import ingest as ingest_router
from app.routers.ingest import _ingest_tx
from app.services.ingest import ingest_one
from tests.test_ingest_e2e import db, payload, utcnow, with_util   # noqa: F401

# An entry `ingest_one` cannot digest: `payload.get("account") or {}` lets a number
# through (it is truthy), and `acct.get("uuid")` on it raises AttributeError. No
# monkeypatch, because this is the real shape of a "permanently bad entry".
ZLY_WPIS = {"account": 123}


@pytest_asyncio.fixture(autouse=True)
async def _swieza_blokada(monkeypatch):
    """A module-level `asyncio.Lock` binds to the event loop at the first CONFLICT (since
    3.10 `acquire()` has a contention-free fast path ahead of `_get_loop()`), while
    pytest-asyncio gives every test its OWN loop. A production process keeps one loop for
    its whole life, so a module-level lock is correct there; today only this file can
    manage to cause a conflict, so the fixture sits HERE and not in a global conftest.py,
    which this repo does not have. Should some other test file start conflicting too, this
    is the fixture to move to conftest.py."""
    monkeypatch.setattr(ingest_router, "_WRITE_LOCK", asyncio.Lock())


@pytest_asyncio.fixture
async def api(db, monkeypatch):
    """The real endpoint, because the backlog loop lives in the router, not in `ingest_one`.
    No SSO patching — `/api/ingest` hangs off `require_ingest_token` alone."""
    import app.main as main
    from app.db import get_session

    monkeypatch.setattr(settings, "ingest_tokens_raw", "t:desktop")
    main.app.dependency_overrides[get_session] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=main.app),
                           base_url="http://test") as c:
        yield c
    main.app.dependency_overrides.clear()


async def post(api, body):
    return await api.post("/api/ingest", json=body,
                          headers={"Authorization": "Bearer t"})


async def ile(db, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def probki_o(db, ts) -> int:
    """Samples exactly at that captured_at. A bare count over the whole table is not enough
    here: every request also appends the CURRENT measurement, so the total grows either way."""
    return (await db.execute(
        select(func.count()).select_from(LimitSample)
        .where(LimitSample.captured_at == ts)
    )).scalar_one()


# -------------------------------------------------------- `backlogAccepted` accounting
async def test_wpis_niebedacy_obiektem_jest_liczony(api, db):
    """A prefix, not a success count: without the increment the probe would cut the wrong
    piece off the spool."""
    now = utcnow()
    p = payload(captured_at=now)
    p["backlog"] = ["nie-obiekt", payload(captured_at=now - timedelta(minutes=5))]
    r = await post(api, p)
    assert r.status_code == 200
    # 2, not 1: position 0 can never be written, so the count admits to it anyway.
    assert r.json()["backlogAccepted"] == 2


async def test_stop_na_pierwszym_niezapisanym_wpisie(api, db):
    """`break`, not `continue` — otherwise `accepted` covers an entry that was never written."""
    now = utcnow()
    p = payload(captured_at=now)
    p["backlog"] = [
        payload(captured_at=now - timedelta(minutes=9)),   # 0 — passes
        ZLY_WPIS,                                          # 1 — fails
        payload(captured_at=now - timedelta(minutes=3)),   # 2 — must NOT be counted
    ]
    r = await post(api, p)
    assert r.status_code == 200
    assert r.json()["backlogAccepted"] == 1
    # The live measurement + entry 0. Entry 1 reached `db.add(batch)` and `flush()`, but
    # its transaction's rollback took it away; entry 2 was never touched. The tail comes
    # back with the next request.
    assert await ile(db, IngestBatch) == 2


async def test_blad_w_backlogu_nie_cofa_pomiaru_zywego(api, db):
    """One transaction per entry. The key assertion here is the one on the NUMBER of
    batches: without a rollback the bad entry's batch row stays in the session and the
    shared `commit()` at the end of the request writes it along with the rest — a batch
    lands in the database for a measurement that never happened. `ok`/`backlogAccepted`/the
    sample count alone cannot tell that apart."""
    p = payload()
    p["backlog"] = [ZLY_WPIS]
    r = await post(api, p)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["backlogAccepted"] == 0
    assert await ile(db, LimitSample) > 0
    # 1, not 2: the bad entry's batch was rolled back along with its transaction.
    assert await ile(db, IngestBatch) == 1


# ---------------------------------------------------------------------- idempotence
async def test_powtorka_ze_spoola_nie_dubluje_probki(api, db):
    """The response was lost, the probe did not truncate the spool, the same entry arrives
    a second time.

    The entry's value MUST differ from the current measurement, otherwise dedup throws it
    out entirely (that is a separate finding, F5) and the test would pass vacuously — hence
    `with_util` and hence the `po_pierwszym > 0` assertion."""
    now = utcnow().replace(microsecond=0)
    ts = now - timedelta(minutes=20)
    stary = payload(usage=with_util(five_hour=0.11), captured_at=ts)

    p1 = payload(captured_at=now)
    p1["backlog"] = [stary]
    assert (await post(api, p1)).status_code == 200
    po_pierwszym = await probki_o(db, ts)
    assert po_pierwszym > 0, "wpis z backlogu nie zapisal nic — test nie sprawdza niczego"

    # The same entry content, a new current measurement — exactly what the probe does
    # after a timeout.
    p2 = payload(captured_at=now + timedelta(minutes=2))
    p2["backlog"] = [stary]
    r = await post(api, p2)
    assert r.status_code == 200
    # The entry MUST be counted so that the probe eventually truncates the spool...
    assert r.json()["backlogAccepted"] == 1
    # ...but it must not append a second row at the same captured_at.
    assert await probki_o(db, ts) == po_pierwszym


async def test_dwa_rozne_pomiary_w_tej_samej_sekundzie_przechodza(api, db):
    """`parse_ts` truncates to whole seconds, and parallel hooks can fire two probes within
    the same second. The guard has no right to delete the second, DIFFERENT measurement —
    which is why the key holds a sha256 of the payload, not just the time. Without that
    condition the answer here comes out as 1."""
    now = utcnow().replace(microsecond=0)
    a = payload(usage=with_util(five_hour=0.11), captured_at=now)
    b = payload(usage=with_util(five_hour=0.44), captured_at=now)

    p = payload(captured_at=now + timedelta(minutes=1))
    p["backlog"] = [a, b]
    r = await post(api, p)
    assert r.status_code == 200
    assert r.json()["backlogAccepted"] == 2
    assert await probki_o(db, now) >= 2


# -------------------------------------------------------------- identity map invariant
async def test_expire_all_zdejmuje_nieswiezy_obiekt_z_identity_map(tmp_path):
    """Someone else's commit in the gap, when the lock is released between two transactions
    of the same request. Without `db.expire_all()` in `_ingest_tx`, session A holds a
    `Machine` object with `batches=1`, does not see session B's increment and overwrites it
    with its own `1 + 1` — the last assertion then shows 2 instead of 3. A lost increment,
    no error at all.

    TWO things in the construction are critical, and each has already broken this test once:

    1. The `trzymana` reference is taken INSIDE `_ingest_tx`. Had the `select()` stood
       outside it, autobegin would have opened a transaction on session A that nobody
       closes — the snapshot would be pinned from before B's commit, and that read would
       also refresh the very object `expire_all()` had just expired. The test would then
       fail ALWAYS, patch or no patch. The value is read into a plain int before leaving
       the block, because after `expire_all()` attribute access would go to the database.
    2. The reference is held at all because the shipped code does not do it: `ingest_one`
       returns only scalars, so the object usually dies with the frame and the weak
       identity map evicts it. That is exactly why that line is in the patch — correctness
       that depends on refcounting having cleaned up is a condition written down nowhere
       and protected by nothing.

    The database is FILE-backed so that the two sessions get two SEPARATE connections: for
    `:memory:` SQLAlchemy picks StaticPool and both sessions sit on one connection, so
    "session B's commit" would commit session A's work as well. The identity map mechanism
    itself could be shown there too, but it would model something other than a foreign
    request."""
    url = "sqlite+aiosqlite:///%s" % (tmp_path / "ingest.db").as_posix()
    silnik = create_async_engine(url)
    async with silnik.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fabryka = async_sessionmaker(silnik, expire_on_commit=False)   # as shipped

    now = utcnow().replace(microsecond=0)
    try:
        async with fabryka() as a, fabryka() as b:
            async with _ingest_tx(a):
                await ingest_one(a, machine_name="desktop", payload=payload(captured_at=now))
                trzymana = (await a.execute(select(Machine))).scalars().one()
                po_pierwszym = trzymana.batches
            assert po_pierwszym == 1

            # Session B — a different connection, its own transaction, its own identity map.
            async with _ingest_tx(b):
                await ingest_one(b, machine_name="desktop",
                                 payload=payload(captured_at=now + timedelta(seconds=1)))

            # Session A returns to the same row, just as with the second backlog entry.
            async with _ingest_tx(a):
                await ingest_one(a, machine_name="desktop",
                                 payload=payload(captured_at=now + timedelta(seconds=2)))
            assert trzymana is not None      # the reference must live to the end

        async with fabryka() as c:
            batches = (await c.execute(select(Machine.batches))).scalar_one()
        assert batches == 3, "inkrement z cudzego commitu zostal nadpisany"
    finally:
        await silnik.dispose()
