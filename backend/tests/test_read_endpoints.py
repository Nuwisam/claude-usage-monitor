"""Diagnostic endpoints — a regression on migration `0002_cli_source` knocking them over.

Version 3 of the probe sends no request at all to Anthropic, so the migration dropped the
columns describing the HTTP response (`http_status`, `request_id`, `rl_status`) and
`cc_version`, which held a constant out of the code anyway. `routers/read.py` kept reading
them — so `/api/machines` and `/api/batches` returned 500 from the moment the migration
was deployed.

No test caught it, because NO test touched these endpoints. Diagnostics were deliberately
left to `curl` (README, `docs/API.md` § 10); it simply followed from that decision, in
silence, that nobody checks them. This file closes the hole: it calls every read endpoint
and checks that it answers at all.

That is why the assertions are deliberately shallow — this is not a test of the shape of a
diagnostic response, but a lookout for "a column vanished from the model and the router
still reads it". Such a bug always shows up on the very first call.
"""
from datetime import timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.test_ingest_e2e import (  # noqa: F401 — the `db` fixture comes along with these
    ACCOUNT_MAX, db, ingest_one, payload, utcnow,
)


@pytest_asyncio.fixture
async def api(db):
    """The app with a swapped session and SSO skipped — as in test_history_endpoint."""
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
    now = utcnow()
    for offset in (120, 0):
        await ingest_one(db, machine_name="desktop",
                         payload=payload(captured_at=now - timedelta(seconds=offset)))
    await db.commit()


async def test_every_read_endpoint_responds(api, data):
    """One loop over them all — a 500 from a dead column shows up here immediately."""
    for path in ("/api/me", "/api/accounts", "/api/machines", "/api/series",
                 "/api/batches", "/api/events", "/api/stats"):
        r = await api.get(path)
        assert r.status_code == 200, "%s -> %s %s" % (path, r.status_code, r.text)


async def test_batches_carry_measurement_provenance_instead_of_http_codes(api, data):
    """The only place that shows whether a measurement had fresh percentages from stdout
    (`cli_merged`) or only the cache went out (`cli_usage_cache`, up to 5 min old). Without
    it a resolution drop from one minute to five is undetectable — data keeps flowing."""
    b = (await api.get("/api/batches")).json()
    assert b, "ingest must have left batches behind"
    assert b[0]["measurementSource"] == "cli_merged"
    assert b[0]["cacheAgeS"] == 42 and b[0]["freshAgeS"] == 7
    assert b[0]["scriptVersion"] == 5
    # Fields left by the dead columns must not come back through a side door as None.
    assert not ({"httpStatus", "requestId", "rlStatus", "ccVersion"} & set(b[0]))


async def test_machines_does_not_pretend_to_know_claude_code_version(api, data):
    """`cc_version` came from the probe's UA_VERSION, i.e. from a constant baked into the
    code — the same value for every machine, independent of what actually runs there.
    `scriptVersion` stays because it tells the truth: it shows the probe's rollout state."""
    m = (await api.get("/api/machines")).json()
    assert m and m[0]["scriptVersion"] == 5
    assert "ccVersion" not in m[0]
