"""Measurement dating: two sources of different age, one anchor on the server side.

A probe measurement glues together values from two places — the `claude -p "/usage"` dump
(percentages of the main windows only) and the Claude Code cache (everything, including
`spend` and `extra_usage`). The sources have DIFFERENT AGES: a dump may be up to 900 s old,
the cache up to 3600 s. As long as both rode on one stamp taken from the dump, `spend` and
`extra_usage` were being rejuvenated by that entire difference.

This is not cosmetics. The backend decides by that stamp which reading is CURRENT
(`newest`, services/ingest.py), and the monotonicity guard requires a known window boundary
on both ends — which these two series NEVER have. They are therefore the only two series
with no defense at all, and they happen to be the binding limit on the Team account.

TWO machines and perfectly synchronized clocks are enough.
"""
import copy
import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import IngestBatch, IngestEvent, LimitSample, SeriesState, UsageSeries
from app.parsing import parse_usage, probe_key
from app.services.ingest import ingest_one, measured_at, request_offset, utcnow
from tests.team import ACCOUNT_TEAM_REAL, USAGE_ACTIVE, usage
from tests.test_ingest_e2e import count, db, payload, send   # noqa: F401

PROBE = Path(__file__).resolve().parents[2] / "client" / "usage-probe.py"


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("usage_probe", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def team(spend_percent=None, five_hour=None):
    u = usage(USAGE_ACTIVE)
    if spend_percent is not None:
        u["spend"]["percent"] = spend_percent
    if five_hour is not None:
        u["five_hour"]["utilization"] = five_hour
    return u


async def _state(db, series_key):
    return (await db.execute(
        select(SeriesState).join(UsageSeries, UsageSeries.id == SeriesState.series_id)
        .where(UsageSeries.series_key == series_key)
    )).scalars().first()


# ------------------------------------------------------------------- main regression
async def test_older_cache_does_not_roll_back_spend_despite_fresher_dump(db):
    """THE POINT OF THE WHOLE CHANGE.

    Machine A: cache 3 min ago (spend 93%), dump 1 min ago.
    Machine B: cache 4 min ago (spend 92%), dump 30 s ago.

    B has the fresher DUMP, so its windows are newer and must win. But its CACHE is older,
    so `spend` must stay with A. Before this change one stamp for the whole measurement
    handed B everything and the state fell back to a reading a minute older — and with the
    real age limits of the two sources that reached hours.
    """
    t = utcnow().replace(microsecond=0)

    await send(db, payload(account=ACCOUNT_TEAM_REAL, usage=team(spend_percent=93, five_hour=50.0),
                           captured_at=t - timedelta(seconds=180),
                           fresh_at=t - timedelta(seconds=60),
                           fresh_covered=["bucket:five_hour", "bucket:seven_day"],
                           sent_at=t), machine="A", arrived_at=t)
    await db.commit()

    await send(db, payload(account=ACCOUNT_TEAM_REAL, usage=team(spend_percent=92, five_hour=51.0),
                           captured_at=t - timedelta(seconds=240),
                           fresh_at=t - timedelta(seconds=30),
                           fresh_covered=["bucket:five_hour", "bucket:seven_day"],
                           sent_at=t), machine="B", arrived_at=t)
    await db.commit()

    spend = await _state(db, "spend:org")
    assert float(spend.last_utilization) == 93.0, "older cache must not roll back spend"
    assert spend.last_captured_at == t - timedelta(seconds=180)

    five = await _state(db, "bucket:five_hour")
    assert float(five.last_utilization) == 51.0, "fresher dump MUST win for the window"
    assert five.last_captured_at == t - timedelta(seconds=30)

    # History loses nothing — B's sample on `spend` is in the database, it just does not
    # govern the state.
    stamps = {s.captured_at for s in (await db.execute(
        select(LimitSample).where(LimitSample.series_id == spend.series_id)
    )).scalars()}
    assert t - timedelta(seconds=240) in stamps


async def test_without_fresh_covered_everything_follows_cache(db):
    """Graceful degradation, not a compatibility branch: a payload without `fresh_covered`
    is dated entirely by `captured_at`, that is by the cache. This is how spool entries
    written by a probe below v5 get through."""
    t = utcnow().replace(microsecond=0)
    await send(db, payload(account=ACCOUNT_TEAM_REAL, usage=team(),
                           captured_at=t - timedelta(seconds=300),
                           fresh_at=t - timedelta(seconds=10), sent_at=t),
               arrived_at=t)
    await db.commit()
    stamps = {s.captured_at for s in (await db.execute(select(LimitSample))).scalars()}
    assert stamps == {t - timedelta(seconds=300)}


@pytest.mark.parametrize("garbage", [None, 7, "bucket:five_hour", {"a": 1}, [1, None, {}]])
async def test_fresh_covered_of_wrong_type_does_not_break_write(db, garbage):
    """`frozenset(None)` raises TypeError, and `ingest_one` has no try/except despite the
    promise in its docstring: for the current record that is a 500 on the whole request, for
    a backlog entry a `break` and a PERMANENTLY blocked spool tail. The endpoint is exposed
    on the internet."""
    p = payload(account=ACCOUNT_TEAM_REAL, usage=team())
    p["measurement"]["fresh_covered"] = garbage
    r = await send(db, p)
    await db.commit()
    assert r["ok"] and r["samples_written"] > 0


# --------------------------------------------------- probe <-> backend key agreement
def test_probe_key_matches_probe_keys(probe):
    """A divergence here is SILENT: the sets would never match, `covered_by_fresh` would
    never light up and dating would quietly fall back to the state from before this change.
    The fixture carries a `weekly_scoped` limit with the model "Fable" — exactly the case in
    which slugging (`limit_series_key` turns it into `fable`) would drive the two ends
    apart."""
    u = usage(USAGE_ACTIVE)
    _, covered = probe.merge(copy.deepcopy(u),
                             {"session": 48, "weekly_all": 47, "scoped": {"Fable": 3}})
    assert covered, "fixture must cover something, otherwise the test checks nothing"

    keys = {probe_key(o) for o in parse_usage(u).observations}
    assert set(covered) <= keys


def test_probe_key_does_not_slug_and_does_not_know_surface():
    """Three properties whose breakage topples nothing — it only switches the mechanism off."""
    u = {"limits": [{"kind": "weekly_scoped", "group": "weekly", "percent": 3,
                     "scope": {"model": {"display_name": "Fable"},
                               "surface": {"display_name": "Cowork"}}}],
         "spend": {"percent": 93, "enabled": True}}
    by_source = {o.source: probe_key(o) for o in parse_usage(u).observations}
    assert by_source["limit"] == "limit:weekly_scoped:Fable"      # the raw name, no slug
    assert by_source["spend"] is None                             # the dump never knows it


def test_probe_key_for_limit_without_model():
    u = {"limits": [{"kind": "session", "group": "session", "percent": 48, "scope": None}]}
    o = parse_usage(u).observations[0]
    assert probe_key(o) == "limit:session:-"


# ---------------------------------------------------------------------------- anchor
def test_measured_at_is_a_pure_function():
    t = utcnow().replace(microsecond=0)
    # The client clock is an hour behind; the measurement is 120 s old on ITS clock.
    clock = t - timedelta(hours=1)
    assert measured_at(clock - timedelta(seconds=120), t - clock, t) == t - timedelta(seconds=120)
    # Clamping: a measurement cannot be newer than the moment it was received.
    assert measured_at(t + timedelta(seconds=10), timedelta(0), t) == t
    # A missing time means "not known", not "now".
    assert measured_at(None, timedelta(0), t) is None


async def test_anchor_is_shared_for_the_whole_request(db):
    """The handler takes the anchor BEFORE the write lock. Computed inside `ingest_one` —
    that is, already under the lock — it would give a request that waited out someone else's
    backlog a stamp too fresh by the waiting time, and every backlog entry its own, different
    anchor."""
    arrived = utcnow().replace(microsecond=0) - timedelta(seconds=30)
    p = payload(account=ACCOUNT_TEAM_REAL, usage=team(),
                captured_at=arrived, sent_at=arrived)
    await ingest_one(db, machine_name="desktop", payload=p, arrived_at=arrived,
                     offset=request_offset(p, arrived))
    await db.commit()
    b = (await db.execute(select(IngestBatch))).scalars().one()
    assert b.received_at == arrived
    assert b.clock_offset_s == 0


# ------------------------------------------------------------------ an old backlog
async def test_entry_from_eight_days_ago_stays_old(db):
    """`BACKLOG_MAX_AGE_SEC` used to substitute the server time for such a measurement —
    the exact opposite of protection: the entry became the newest one and took over the
    current state."""
    t = utcnow().replace(microsecond=0)
    long_ago = t - timedelta(days=8)

    await send(db, payload(account=ACCOUNT_TEAM_REAL, usage=team(spend_percent=93),
                           captured_at=t - timedelta(seconds=60), sent_at=t), arrived_at=t)
    await db.commit()

    # `offset` is SHARED by the request and comes from the outer record, not from the entry:
    # it is the current record that was sent just now. The entry's own `sent_at` serves only
    # the check "the measurement did not come into being after the send".
    p = payload(account=ACCOUNT_TEAM_REAL, usage=team(spend_percent=10),
                captured_at=long_ago, sent_at=long_ago)
    await ingest_one(db, machine_name="desktop", payload=p, arrived_at=t,
                     offset=timedelta(0), is_backlog=True)
    await db.commit()

    stamps = {s.captured_at for s in (await db.execute(select(LimitSample))).scalars()}
    assert long_ago in stamps, "an old measurement must be saved with its own date"
    spend = await _state(db, "spend:org")
    assert float(spend.last_utilization) == 93.0, "an old entry must not take over the state"


async def test_entry_with_clock_turned_back_is_rejected_but_counted(db):
    """A measurement cannot have come into being after the send. The whole entry is rejected
    — otherwise it would land on the request's anchor, pass `newest` and overwrite the state
    with an old reading."""
    t = utcnow().replace(microsecond=0)
    p = payload(account=ACCOUNT_TEAM_REAL, usage=team(spend_percent=10),
                captured_at=t - timedelta(seconds=60),
                sent_at=t - timedelta(seconds=600))
    r = await ingest_one(db, machine_name="desktop", payload=p, arrived_at=t,
                         offset=timedelta(0), is_backlog=True)
    await db.commit()

    assert not r["ok"] and r["samples_written"] == 0
    assert await count(db, LimitSample) == 0
    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "clock_backwards" in types
    b = (await db.execute(select(IngestBatch))).scalars().one()
    assert b.raw_payload_id is not None, "raw payload goes to the database anyway (rule 6)"


async def test_entry_without_sent_at_passes_despite_newer_date(db):
    """A spool entry written by probe v4 carries the DUMP time in `captured_at`, routinely
    NEWER than the `captured_at` of the current record (which since v5 is the cache time).
    The rejection criterion stands on `sent_at` precisely so that such entries survive."""
    t = utcnow().replace(microsecond=0)
    p = payload(account=ACCOUNT_TEAM_REAL, usage=team(spend_percent=88),
                captured_at=t - timedelta(seconds=10))
    r = await ingest_one(db, machine_name="desktop", payload=p, arrived_at=t,
                         offset=timedelta(0), is_backlog=True)
    await db.commit()
    assert r["ok"] and r["samples_written"] > 0
