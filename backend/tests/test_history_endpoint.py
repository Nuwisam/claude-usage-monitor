"""The time boundary on the WAY IN to /api/history.

Regression on a bug seen live: the History view returned 500 on every open.

Contract v2 attached a zone to the OUTGOING time — and because of that the browser began
SENDING IT BACK, since `Date.toISOString()` ends in 'Z'. Pydantic turned that into a
zone-aware datetime, while the database, `utcnow()` and the samples are naive in UTC. The
subtraction in `_quiet_intervals` fell over on "can't subtract offset-naive and
offset-aware datetimes".

Unit tests did not catch it, because they call `_find_gaps` directly, passing naive
datetimes — that is, they bypass exactly the layer in which the bug arises. Which is why
these tests go over HTTP: the parameter must pass FastAPI validation, as from a browser.

The second, worse case is covered by `test_a_non_utc_offset_is_converted_not_truncated`: the MySQL driver
formats datetimes with `strftime` and IGNORES tzinfo, so a parameter carrying '+02:00'
would not blow up — it would quietly shift the whole range by two hours.
"""
from datetime import timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.test_ingest_e2e import (  # noqa: F401 — the `db` fixture comes along with these
    ACCOUNT_MAX, db, ingest_one, payload, utcnow,
)

from app.models import UsageSeries


@pytest_asyncio.fixture
async def api(db):
    """The app with a swapped session and SSO skipped. httpx instead of TestClient, to
    share the event loop with the `db` fixture — the aiosqlite session belongs to it."""
    import app.main as main
    from app.db import get_session
    from app.sso import CurrentUser, require_authorized_user

    main.app.dependency_overrides[get_session] = lambda: db
    main.app.dependency_overrides[require_authorized_user] = lambda: CurrentUser(
        email="a@b.pl", verified_at=None
    )
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    main.app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def data(db):
    """A few samples in the 5 h window. The query range is far wider than the data, so a
    `client_silent` gap appears — precisely the path that used to fall over."""
    now = utcnow()
    for offset in (240, 120, 0):
        await ingest_one(db, machine_name="desktop",
                         payload=payload(captured_at=now - timedelta(seconds=offset)))
    await db.commit()
    sid = (await db.execute(
        select(UsageSeries.id).where(UsageSeries.series_key == "bucket:five_hour")
    )).scalar_one()
    return {"account": ACCOUNT_MAX["uuid"], "seriesId": sid, "now": now}


def _iso(dt, suffix="Z"):
    return dt.isoformat() + suffix


async def test_a_range_with_a_zone_does_not_crash_the_endpoint(api, data):
    """Exactly what the browser sends: `Date.toISOString()`, that is, the 'Z' suffix."""
    r = await api.get("/api/history", params={
        "account": data["account"], "seriesId": data["seriesId"],
        "from": _iso(data["now"] - timedelta(hours=2)),
        "to": _iso(data["now"]),
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["points"], "samples from the window must be found in the range"
    assert any(g["kind"] == "client_silent" for g in body["gaps"]), \
        "an empty start of the range is client silence — and that was the path that used to crash"


async def test_notation_with_and_without_a_zone_gives_the_same_result(api, data):
    """Naive UTC already worked and must keep working — conversion must not change meaning."""
    args = {"account": data["account"], "seriesId": data["seriesId"]}
    frm, to = data["now"] - timedelta(hours=2), data["now"]

    with_zone = await api.get("/api/history", params={**args, "from": _iso(frm), "to": _iso(to)})
    without_zone = await api.get("/api/history", params={**args, "from": frm.isoformat(),
                                                "to": to.isoformat()})
    assert with_zone.status_code == without_zone.status_code == 200
    assert with_zone.json() == without_zone.json()


async def test_a_non_utc_offset_is_converted_not_truncated(api, data):
    """The same INSTANT written in a different zone must give the same answer.

    This is the silent case: if the offset were ignored instead of converted, the range
    would shift by two hours, and the response would still be HTTP 200 and would look
    correct. No symptom, wrong data — the very failure mode this whole project
    defends against.
    """
    args = {"account": data["account"], "seriesId": data["seriesId"]}
    frm, to = data["now"] - timedelta(hours=2), data["now"]

    utc = await api.get("/api/history", params={**args, "from": _iso(frm), "to": _iso(to)})
    # the same instant, written as local time
    plus2 = await api.get("/api/history", params={
        **args,
        "from": _iso(frm + timedelta(hours=2), "+02:00"),
        "to": _iso(to + timedelta(hours=2), "+02:00"),
    })
    assert utc.status_code == plus2.status_code == 200, plus2.text
    assert utc.json() == plus2.json()


async def test_missing_range_still_works(api, data):
    """The default takes `utcnow()`, i.e. a NAIVE time — this path must not suffer as a
    side effect of the fix. `bucket=raw` explicitly, because downsampling rests on
    MariaDB functions (`unix_timestamp`, `group_concat ... ORDER BY`) SQLite lacks."""
    r = await api.get("/api/history", params={
        "account": data["account"], "seriesId": data["seriesId"], "bucket": "raw",
    })
    assert r.status_code == 200, r.text
    assert r.json()["points"]
