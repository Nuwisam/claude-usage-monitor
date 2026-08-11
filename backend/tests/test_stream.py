"""SSE stream — who receives what, and what they must NOT receive.

The single most important test here is `test_subscription_receives_only_its_own_accounts`.
Everything else guards traps that are invisible in this path until production: publishing
before the commit, events muted by dedup, and the rule that a broken stream must never
bring ingest down.

TECHNIQUE: httpx's `ASGITransport` BUFFERS the whole response body — it waits for the app
to finish and only then returns a `Response`. An endless stream would hang the test
forever, so every test shortens `STREAM_MAX_LIFETIME_SEC` to a fraction of a second; the
connection ends with a `bye` frame and we parse the whole thing off the wire. The side
effect is welcome: we test the real SSE format, not an in-memory structure.
"""
from __future__ import annotations

import asyncio
import json
from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.test_ingest_e2e import (  # noqa: F401 — the `db` fixture comes along with these
    ACCOUNT_MAX, ACCOUNT_TEAM, db, ingest_one, payload, utcnow, with_util,
)

from app.config import settings
from app.services.events import broker

UUID_MAX = ACCOUNT_MAX["uuid"]
UUID_TEAM = ACCOUNT_TEAM["uuid"]


# --------------------------------------------------------------------------- helpers
@pytest_asyncio.fixture(autouse=True)
async def clean_broker():
    """The broker is a module-level singleton — without this a leak from one test would
    feed the next, and "a frame arrived" would sometimes be true for the wrong reason."""
    yield
    for sub in list(broker._subs):
        broker.unsubscribe(sub)
    assert broker.count == 0


@pytest_asyncio.fixture
async def api(db, monkeypatch):
    import app.main as main
    import app.routers.stream as stream_router
    import app.sso as sso
    from app.db import get_session
    from app.sso import CurrentUser, require_authorized_user

    # The stream endpoint deliberately does not take its session via Depends (it must not
    # hold a pooled connection for the whole connection lifetime), so we swap the factory.
    #
    # `snapshot_done` fires when the endpoint releases the session, i.e. when the hello
    # frame and the snapshot are on the wire. Tests wait for it before triggering an
    # ingest: in production the two requests use separate sessions, but here they share
    # one, and an AsyncSession cannot serve two operations at once. Waiting on a real
    # signal rather than a guessed sleep also removes the ordering race outright.
    snapshot_done = asyncio.Event()

    class _Session:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            snapshot_done.set()
            return False

    monkeypatch.setattr(stream_router, "SessionLocal", _Session)
    # Fixed ingest token so the tests do not depend on the environment.
    monkeypatch.setattr(settings, "ingest_tokens_raw", "t:desktop")

    main.app.dependency_overrides[get_session] = lambda: db
    main.app.dependency_overrides[require_authorized_user] = lambda: CurrentUser(
        email="a@b.pl", verified_at=None
    )
    # `require_stream_client` calls SSO DIRECTLY rather than through Depends — the path is
    # chosen by the presence of an `Authorization` header, so with a token the gate must
    # not be touched at all. Consequence: `dependency_overrides` does not cover it,
    # and without this patch the tests reached the real SSO (getaddrinfo failed).
    async def _user(request=None):
        return CurrentUser(email="a@b.pl", verified_at=None)

    monkeypatch.setattr(sso, "require_authorized_user", _user)

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        c.snapshot_done = snapshot_done
        yield c
    main.app.dependency_overrides.clear()


def parse_sse(text: str) -> list[tuple[str, dict | None]]:
    """Raw SSE stream -> list of (event, data)."""
    out: list[tuple[str, dict | None]] = []
    event: str | None = None
    data: list[str] = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data.append(line[6:])
        elif line == "" and event is not None:
            out.append((event, json.loads("\n".join(data)) if data else None))
            event, data = None, []
    return out


def cards(events) -> list[dict]:
    return [d["account"] for e, d in events if e == "account"]


async def listen(api, monkeypatch, *, query: str, work=None, lifetime=0.4, ping=0.05):
    """Opens the stream, runs `work()` once the snapshot is out, returns the events.

    Ordering matters twice over: `work` (usually an ingest) must start only after the
    client is in the broker AND after the endpoint has released the shared test session.
    Both are awaited on real signals rather than a guessed `sleep` — otherwise the test
    would sometimes be green because of a race rather than because of correctness.
    """
    monkeypatch.setattr(settings, "stream_max_lifetime_sec", lifetime)
    monkeypatch.setattr(settings, "stream_ping_sec", ping)

    task = asyncio.create_task(api.get("/api/stream?%s" % query))
    try:
        for _ in range(400):
            if broker.count:
                break
            await asyncio.sleep(0.005)
        else:
            task.cancel()
            raise AssertionError("client never registered with the broker")

        await asyncio.wait_for(api.snapshot_done.wait(), timeout=5)
        if work is not None:
            await work()
        r = await asyncio.wait_for(task, timeout=10)
    except BaseException:
        task.cancel()
        raise
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    return parse_sse(r.text)


async def write(api, account=None, usage=None, captured_at=None):
    """Goes through the REAL endpoint, not `ingest_one` — publication lives in the router,
    after the commit, so calling the service directly would silently test nothing."""
    r = await api.post(
        "/api/ingest",
        json=payload(account=account or ACCOUNT_MAX, usage=usage,
                     captured_at=captured_at),
        headers={"Authorization": "Bearer t"},
    )
    assert r.status_code == 200, r.text
    return r.json()


# --------------------------------------------------------------------------- the point
async def test_subscription_receives_only_its_own_accounts(api, db, monkeypatch):
    """THE whole point of this feature. Subscribed to the Max account, a measurement lands
    for the Team account — not a single card may appear in the stream.

    If the filter ever stopped working the on-screen symptom would be zero (the UI sees
    both accounts anyway), while the desk panel would be fed someone else's data without a
    word of warning."""
    await write(api, ACCOUNT_MAX)
    await write(api, ACCOUNT_TEAM)

    async def foreign():
        await write(api, ACCOUNT_TEAM, usage=with_util(five_hour=71.0))

    events = await listen(api, monkeypatch,
                          query="account=%s&snapshot=0" % UUID_MAX, work=foreign)
    assert cards(events) == [], "a foreign account's measurement leaked into the stream"
    assert [e for e, _ in events if e == "hello"], "missing hello frame"


async def test_own_measurement_arrives(api, db, monkeypatch):
    # Timestamps a minute apart, not two writes inside the same second: `parse_ts`
    # truncates to whole seconds, so same-second measurements are not "newer" than one
    # another and `SeriesState` is deliberately left alone. The probe throttles to 60 s,
    # so this spacing is what production actually looks like.
    t0 = (utcnow() - timedelta(seconds=120)).replace(microsecond=0)
    await write(api, ACCOUNT_MAX, captured_at=t0)

    async def own():
        await write(api, ACCOUNT_MAX, usage=with_util(five_hour=71.0),
                    captured_at=t0 + timedelta(seconds=60))

    events = await listen(api, monkeypatch,
                          query="account=%s&snapshot=0" % UUID_MAX, work=own)
    c = cards(events)
    assert len(c) == 1 and c[0]["uuid"] == UUID_MAX
    series = {s["seriesKey"]: s for s in c[0]["series"]}["bucket:five_hour"]
    assert series["rawUtilization"] == pytest.approx(71.0)


async def test_two_accounts_sharing_one_email_stay_separate(api, db, monkeypatch):
    """Regression on the very reason the key is a UUID and not an email: one address
    really does point at several accounts (Pro and a Team seat). Matching on the string
    would deliver both accounts' frames here and nobody would notice, because the email on
    screen would line up perfectly."""
    second = dict(ACCOUNT_TEAM, email=ACCOUNT_MAX["email"])
    await write(api, ACCOUNT_MAX)
    await write(api, second)

    async def foreign():
        await write(api, second, usage=with_util(five_hour=64.0))

    events = await listen(api, monkeypatch,
                          query="account=%s&snapshot=0" % UUID_MAX, work=foreign)
    assert cards(events) == [], "a shared email must not join two accounts"


async def test_many_accounts_in_one_subscription(api, db, monkeypatch):
    await write(api, ACCOUNT_MAX)
    await write(api, ACCOUNT_TEAM)

    async def both():
        await write(api, ACCOUNT_MAX, usage=with_util(five_hour=51.0))
        await write(api, ACCOUNT_TEAM, usage=with_util(five_hour=52.0))

    events = await listen(
        api, monkeypatch,
        query="account=%s&account=%s&snapshot=0" % (UUID_MAX, UUID_TEAM), work=both)
    assert {c["uuid"] for c in cards(events)} == {UUID_MAX, UUID_TEAM}


# --------------------------------------------------------------------------- traps
async def test_event_fires_even_when_dedup_skips_the_sample(api, db, monkeypatch):
    """On an unchanged value dedup writes NO sample (`samples_written == 0`) but does move
    `last_confirmed_at` — which is exactly what keeps the state `live`.

    Gating publication on the sample count would mute the stream in the most common case
    of all: when nothing changes. The card on screen would then age despite a working
    client, and that is the very bug contract v3 was created for."""
    t0 = (utcnow() - timedelta(seconds=100)).replace(microsecond=0)
    await write(api, captured_at=t0)

    async def same_value():
        r = await write(api, captured_at=t0 + timedelta(seconds=60))
        assert r["samplesWritten"] == 0, "the scenario was supposed to hit dedup"

    events = await listen(api, monkeypatch,
                          query="account=%s&snapshot=0" % UUID_MAX, work=same_value)
    c = cards(events)
    assert len(c) == 1, "a skipped sample write must not mute the event"
    series = {s["seriesKey"]: s for s in c[0]["series"]}["bucket:five_hour"]
    assert series["confirmedAt"] > series["capturedAt"], \
        "the frame must carry the refreshed CONFIRMATION, not just the sample write"


async def test_frame_carries_post_commit_state(api, db, monkeypatch):
    """Publishing before the commit is a silent race: the receiver would see the pre-write
    state and sit on it until the next poll. Here we check the effect — the frame must
    carry THIS measurement's value, not the previous one."""
    t0 = (utcnow() - timedelta(seconds=120)).replace(microsecond=0)
    await write(api, ACCOUNT_MAX, usage=with_util(five_hour=10.0), captured_at=t0)

    async def change():
        await write(api, ACCOUNT_MAX, usage=with_util(five_hour=88.0),
                    captured_at=t0 + timedelta(seconds=60))

    events = await listen(api, monkeypatch,
                          query="account=%s&snapshot=0" % UUID_MAX, work=change)
    series = {s["seriesKey"]: s for s in cards(events)[0]["series"]}["bucket:five_hour"]
    assert series["rawUtilization"] == pytest.approx(88.0)


async def test_publish_failure_does_not_break_ingest(api, db, monkeypatch):
    """The probe is the only code sitting on the critical path of the user's actual work. The chart
    may break, the session may not."""
    import app.routers.ingest as ingest_router

    async def boom(*a, **kw):
        raise RuntimeError("broker died")

    monkeypatch.setattr(ingest_router, "publish_accounts", boom)
    monkeypatch.setattr(settings, "ingest_tokens_raw", "t:desktop")

    r = await api.post("/api/ingest", json=payload(),
                       headers={"Authorization": "Bearer t"})
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


# --------------------------------------------------------------------------- contract
async def test_snapshot_on_connect(api, db, monkeypatch):
    """Without this the panel sits blank after a restart until the first work session —
    potentially hours."""
    await write(api, ACCOUNT_MAX)
    events = await listen(api, monkeypatch, query="account=%s" % UUID_MAX)
    c = cards(events)
    assert len(c) == 1 and c[0]["uuid"] == UUID_MAX
    assert c[0]["series"], "a snapshot without series is useless"


async def test_snapshot_can_be_disabled(api, db, monkeypatch):
    """The browser has just fetched /api/status, so a snapshot would be a duplicate."""
    await write(api, ACCOUNT_MAX)
    events = await listen(api, monkeypatch, query="account=%s&snapshot=0" % UUID_MAX)
    assert cards(events) == []


async def test_hello_reports_unknown_uuids(api, db, monkeypatch):
    """A typo in a panel's config must not show up as silence indistinguishable from
    idleness — and must not drop the connection, because the account may appear later."""
    await write(api, ACCOUNT_MAX)
    events = await listen(
        api, monkeypatch, query="account=%s&account=no-such-uuid&snapshot=0" % UUID_MAX)
    hello = [d for e, d in events if e == "hello"][0]
    assert hello["subscribed"] == [UUID_MAX]
    assert hello["unknown"] == ["no-such-uuid"]
    assert hello["contractVersion"] == 3
    assert hello["serverNow"].endswith("Z")


async def test_account_created_after_connect_still_arrives(api, db, monkeypatch):
    """We subscribe to UUIDs unknown at connect time too. Switching accounts via `/login`
    is routine here, so an account can come into existence mid-stream — and the publisher
    must then find a listener without any extra machinery."""
    async def new_account():
        await write(api, ACCOUNT_TEAM)

    events = await listen(api, monkeypatch,
                          query="account=%s" % UUID_TEAM, work=new_account)
    hello = [d for e, d in events if e == "hello"][0]
    assert hello["unknown"] == [UUID_TEAM], "the account did not exist at connect time"
    assert [c["uuid"] for c in cards(events)] == [UUID_TEAM]


async def test_ping_and_bye_close_the_stream(api, db, monkeypatch):
    await write(api, ACCOUNT_MAX)
    events = await listen(api, monkeypatch, query="account=%s&snapshot=0" % UUID_MAX,
                          lifetime=0.3, ping=0.05)
    names = [e for e, _ in events]
    assert "ping" in names, "without a ping a dead subscription would sit in the broker"
    assert names[-1] == "bye", "the lifetime limit must close the connection explicitly"
    assert [d for e, d in events if e == "bye"][0]["reason"] == "lifetime"


async def test_missing_account_param_is_400(api, db):
    """A missing parameter must not mean "everything"."""
    r = await api.get("/api/stream")
    assert r.status_code == 400
    assert r.json()["detail"]["reason"] == "no-subscription"


async def test_client_limit(api, db, monkeypatch):
    monkeypatch.setattr(settings, "stream_max_clients", 0)
    r = await api.get("/api/stream?account=%s" % UUID_MAX)
    assert r.status_code == 503
    assert r.json()["detail"]["reason"] == "too-many-streams"


# --------------------------------------------------------------------------- auth
async def test_bearer_opens_the_stream_without_sso(api, db, monkeypatch):
    """The desk panel has no SSO cookie. The token path is selected by the presence of the
    `Authorization` header."""
    import app.main as main
    import app.sso as sso
    from app.sso import require_authorized_user

    # SSO removed entirely — if the endpoint consulted the gate despite the token, this
    # test would fail rather than pass by accident.
    main.app.dependency_overrides.pop(require_authorized_user, None)

    async def must_not_be_called(request=None):
        raise AssertionError("SSO was consulted even though a valid token was presented")

    monkeypatch.setattr(sso, "require_authorized_user", must_not_be_called)
    monkeypatch.setattr(settings, "stream_tokens_raw", "secret:panel")
    monkeypatch.setattr(settings, "stream_max_lifetime_sec", 0.2)
    monkeypatch.setattr(settings, "stream_ping_sec", 0.05)

    r = await api.get("/api/stream?account=%s&snapshot=0" % UUID_MAX,
                      headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert [e for e, _ in parse_sse(r.text) if e == "hello"]


async def test_bad_token_is_401(api, db, monkeypatch):
    monkeypatch.setattr(settings, "stream_tokens_raw", "secret:panel")
    r = await api.get("/api/stream?account=%s" % UUID_MAX,
                      headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == "invalid-token"


async def test_ingest_token_does_not_open_the_stream(api, db, monkeypatch):
    """The probe token is a WRITE-only credential and is handed out on every machine. If it
    also opened reads, its meaning would widen silently — no rotation, no decision, no
    trace."""
    monkeypatch.setattr(settings, "ingest_tokens_raw", "t:desktop")
    monkeypatch.setattr(settings, "stream_tokens_raw", "secret:panel")
    r = await api.get("/api/stream?account=%s" % UUID_MAX,
                      headers={"Authorization": "Bearer t"})
    assert r.status_code == 401


# --------------------------------------------------------------------------- broker
async def test_queue_overflow_yields_lag(db):
    """A slow receiver must not slow ingest down. Intermediate frames are disposable
    because each carries the account's FULL state — only the signal that there was a gap
    needs to survive."""
    sub = broker.subscribe(frozenset({UUID_MAX}), "test")
    try:
        for i in range(settings.stream_queue_max + 5):
            broker.publish(UUID_MAX, "frame-%d" % i)
        collected = []
        while not sub.queue.empty():
            collected.append(sub.queue.get_nowait())
        assert len(collected) < settings.stream_queue_max + 5
        assert any("queue-overflow" in f for f in collected)
    finally:
        broker.unsubscribe(sub)


async def test_no_listeners_means_no_frame_is_built(api, db, monkeypatch):
    """With the browser closed, ingest must pay nothing for the stream — `publish_accounts`
    has to bounce off `has_subscribers` before touching the database."""
    import app.services.events as ev

    async def must_not_be_called(*a, **kw):
        raise AssertionError("a frame was built with no listeners")

    monkeypatch.setattr(ev, "account_frame", must_not_be_called)
    await write(api, ACCOUNT_MAX)
    assert await ev.publish_accounts(db, [UUID_MAX]) == 0
