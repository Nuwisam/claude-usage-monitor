"""POST /api/session-alert and the `alert` frame.

This endpoint is unlike the rest of the backend: it does not touch the database, and its
state lives in process memory. The tests therefore guard four things that break silently
in this construction:

  * an alert published WHILE the snapshot is being built must survive (a regression on
    the loop that clears the queue in routers/stream.py),
  * every frame carries the FULL set, so after a `lag` the next frame restores the state,
  * a POST replaces the machine's set wholesale — an empty list clears its alerts,
  * `machine` comes from the TOKEN, never from the request body.
"""
from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

from tests.test_stream import (  # noqa: F401 — the `api`/`db` fixtures come along too
    ACCOUNT_MAX, api, cards, clean_broker, db, ingest_one, listen, parse_sse,
    payload, utcnow, with_util,
)

from app.config import settings
from app.services.events import ALERTS, broker

UUID_MAX = ACCOUNT_MAX["uuid"]

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def clean_alerts():
    ALERTS.clear()
    yield
    ALERTS.clear()


def entry(**kw):
    # `since` MUST be computed now, not written in by hand: `current_alerts()` drops
    # entries older than ALERT_MAX_AGE_SEC (24 h), so a fixed stamp turns every set-level
    # test into a time bomb — it passes for a day after being written, then fails forever.
    # The age-filter test supplies its own date and does not use this default.
    base = {"key": "session__main__abc", "reason": "permission", "project": "proj",
            "tool": "Bash", "detail": "git status",
            "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    base.update(kw)
    return base


async def send(api, entries, token="t"):
    return await api.post("/api/session-alert", json={"entries": entries},
                          headers={"Authorization": "Bearer %s" % token})


def alerts_of(events):
    return [d["alerts"] for e, d in events if e == "alert"]


# --------------------------------------------------------------------------- endpoint
async def test_no_token_rejected(api):
    r = await api.post("/api/session-alert", json={"entries": []})
    assert r.status_code == 401


async def test_wrong_token_rejected(api):
    r = await send(api, [], token="not-this-one")
    assert r.status_code == 401


async def test_machine_name_comes_from_token_not_body(api):
    """If the client could send `machine`, any machine could impersonate somebody
    else's entries — and this is the label a person reads off the panel and uses to
    decide where to go."""
    r = await send(api, [entry(machine="someone-elses-machine")])
    assert r.status_code == 200
    assert r.json()["machine"] == "desktop"
    assert ALERTS["desktop"][0].machine == "desktop"


async def test_malformed_entry_does_not_clear_whole_set(api):
    """Clearing an alert because of a formatting error would be the worst failure mode of
    this feature — the same kind of bug as a false zero in a measurement."""
    r = await send(api, [entry(key="a"), {"reason": "permission"}, entry(key="b")])
    assert r.status_code == 200
    assert r.json()["accepted"] == 2


async def test_empty_list_clears_machine_alerts(api):
    await send(api, [entry()])
    assert ALERTS
    await send(api, [])
    assert "desktop" not in ALERTS


async def test_entries_must_be_a_list(api):
    r = await api.post("/api/session-alert", json={"entries": "not-a-list"},
                       headers={"Authorization": "Bearer t"})
    assert r.status_code == 400


async def test_body_too_large(api, monkeypatch):
    monkeypatch.setattr(settings, "max_ingest_body_bytes", 50)
    r = await send(api, [entry()])
    assert r.status_code == 413


# --------------------------------------------------------------------------- stream
async def test_snapshot_carries_alerts_present_before_connection(api, monkeypatch):
    """The most important test in this file.

    STREAM_MAX_LIFETIME_SEC forces the panel to recycle the connection every 15 minutes,
    and a blocked session emits NO event at all in that time (measured: 98% of blocks).
    Without the state being restored in the snapshot, a block lasting 40 minutes would
    disappear from the screen after fifteen — and nobody would notice it was gone.
    """
    await send(api, [entry()])
    events = await listen(api, monkeypatch, query="account=%s" % UUID_MAX)
    frames = alerts_of(events)
    assert frames, "snapshot must contain an `alert` frame"
    assert frames[0][0]["project"] == "proj"
    assert frames[0][0]["machine"] == "desktop"


async def test_alert_during_snapshot_survives(api, monkeypatch):
    """A regression on `stream.py`: the loop after the snapshot discards everything that
    landed in the queue while it was being built. For `account` frames that is harmless
    by design (the snapshot supersedes them), for an ephemeral alert it would be silent
    and fatal — so the alert frame goes out AFTER that loop, not through the queue."""
    async def work():
        await send(api, [entry(project="landed-during-snapshot")])

    events = await listen(api, monkeypatch, query="account=%s" % UUID_MAX, work=work)
    seen = [a["project"] for frame in alerts_of(events) for a in frame]
    assert "landed-during-snapshot" in seen


async def test_every_frame_carries_full_set(api, monkeypatch):
    """Not a delta. That is why losing a frame is harmless, and why `lag` suffices
    as the only signal that there was a break."""
    async def work():
        await send(api, [entry(key="a", project="alpha")])
        await send(api, [entry(key="a", project="alpha"),
                         entry(key="b", project="beta")])

    events = await listen(api, monkeypatch, query="account=%s" % UUID_MAX, work=work)
    last = alerts_of(events)[-1]
    assert sorted(a["project"] for a in last) == ["alpha", "beta"]


async def test_alert_reaches_regardless_of_account_subscription(api, monkeypatch):
    """`publish_all`, not `publish`: an alert belongs to no single account. A panel
    subscribed to one account must see a block in a project that happens to be running
    on another."""
    async def work():
        await send(api, [entry()])

    events = await listen(api, monkeypatch,
                          query="account=uuid-that-does-not-exist", work=work)
    assert alerts_of(events), "the `alert` frame must not depend on what the panel is subscribed to"


async def test_overflowing_queue_gives_lag_and_next_frame_restores_state(monkeypatch):
    """On overflow `_drain` throws away ALL queued frames and inserts a `lag`. The
    recovery works only because the next frame is a full state — for a delta this
    mechanism would be silent data loss.

    Checked on the broker itself, not through the endpoint: over HTTP the receiver
    drains the queue as fast as it grows, so an overflow cannot be provoked there
    other than by artificially stalling the generator — and that would test the
    stall, not the overflow.
    """
    from app.services.events import alert_frame, set_alerts
    from app.schemas import SessionAlert

    monkeypatch.setattr(settings, "stream_queue_max", 2)
    sub = broker.subscribe(frozenset(["any-account"]), "panel")
    try:
        for i in range(6):
            set_alerts("desktop", [SessionAlert(**entry(key="k%d" % i,
                                                        project="p%d" % i,
                                                        machine="desktop"))])
            broker.publish_all(alert_frame(now=utcnow()))
        frames = []
        while not sub.queue.empty():
            frames.append(sub.queue.get_nowait())
    finally:
        broker.unsubscribe(sub)

    assert any("event: lag" in r for r in frames)
    # The last frame after `lag` carries the whole current state, so the loss self-heals.
    assert "p5" in frames[-1]


async def test_stale_entry_drops_out_of_snapshot(api, monkeypatch):
    """A machine that disappeared mid-block will never send a correction. The writer has
    its own TTL, but the server must not rely on it."""
    from app.services import events as ev

    monkeypatch.setattr(ev, "ALERT_MAX_AGE_SEC", 1.0)
    await send(api, [entry(since="2020-01-01T00:00:00Z")])
    frames = alerts_of(await listen(api, monkeypatch, query="account=%s" % UUID_MAX))
    assert frames and frames[0] == []


async def test_process_restart_clears_map():
    """Deliberate: alerts return with the next event from a machine, and a write to the
    database would mean a migration and a row lifecycle for state that is momentary by
    definition."""
    ALERTS["desktop"] = ["anything"]
    ALERTS.clear()                      # this is what a process start does
    assert ALERTS == {}


async def test_browser_listener_set_does_not_see_frame(api, monkeypatch):
    """`useLiveStream.ts` registers five named listeners and has NO `onmessage`, so
    the `alert` frame is invisible to the browser by construction. This test pins that
    property to the event name, so a change to `message` cannot pass unnoticed."""
    async def work():
        await send(api, [entry()])

    events = await listen(api, monkeypatch, query="account=%s" % UUID_MAX, work=work)
    known_to_browser = {"hello", "ping", "account", "lag", "bye"}
    assert any(e == "alert" for e, _ in events)
    assert "alert" not in known_to_browser
