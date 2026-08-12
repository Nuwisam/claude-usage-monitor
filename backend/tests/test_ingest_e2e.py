"""End-to-end test of the write path: real payload -> ingest -> series_state -> /api/status.

Uses in-memory SQLite. Does not cover /api/history (the downsampling queries are specific
to MariaDB), but does cover all the logic that can yield BAD DATA: dedup, the monotonicity
guard, account-switch detection and freshness states.
"""
import copy
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import (
    Account, Base, IngestBatch, IngestEvent, LimitSample, RawPayload, SeriesState,
    UsageSeries,
)
from app.services.ingest import ingest_one, request_offset, utcnow
from app.services.status import build_status

REAL = json.loads((Path(__file__).parent / "fixtures" / "usage_max.json").read_text("utf-8"))

ACCOUNT_MAX = {
    "uuid": "00000000-0000-4000-8000-000000000003",
    "email": "you@example.org", "display_name": "Tomasz",
    "org_uuid": "00000000-0000-4000-8000-000000000004",
    "org_type": "claude_max", "org_rate_limit_tier": "default_claude_max_5x",
    "seat_tier": None, "extra_usage_enabled": False,
}
ACCOUNT_TEAM = {
    "uuid": "aaaabbbb-0000-1111-2222-333344445555",
    "email": "work@example.com", "org_type": "claude_team",
    "seat_tier": "premium", "org_rate_limit_tier": "team_premium",
}


_DEFAULT = object()


def payload(usage=None, account=_DEFAULT, captured_at=None, event="PostToolUse",
            sent_at=None, fresh_at=None, fresh_covered=None):
    # a sentinel, not `account or ACCOUNT_MAX` — an empty dict is falsy and would put
    # the default account in its place, making the test "payload with no account" test
    # something else
    meas = {"source": "cli_merged", "cache_age_s": 42, "fresh_age_s": 7}
    # `sent_at` is NOT added here by default — `send` appends it at the moment of sending,
    # exactly like the probe (client/usage-probe.py, just before `post`). Passing it here
    # explicitly serves to model a SKEWED client clock.
    if sent_at is not None:
        meas["sent_at"] = sent_at.isoformat()
    if fresh_at is not None:
        meas["fresh_at"] = fresh_at.isoformat()
    if fresh_covered is not None:
        meas["fresh_covered"] = fresh_covered
    return {
        "account": ACCOUNT_MAX if account is _DEFAULT else account,
        "token_meta": {"subscription_type": "max"},
        "captured_at": (captured_at or utcnow()).isoformat(),
        "client": {"host": "DESKTOP-X", "script_version": 5, "exec_ms": 36},
        "hook": {"event": event, "session_id": "sess-1", "cwd": "z:/projects/x"},
        "measurement": meas,
        "usage": usage if usage is not None else REAL,
    }


async def send(db, p, *, machine="desktop", arrived_at=None, is_backlog=False):
    """Repeats what the handler does: ONE time anchor per request, the offset taken from
    `measurement.sent_at` and computed by the same function the shipped code uses.

    When a payload has no `sent_at`, one equal to the anchor is added — that models a client
    with a synchronized clock, whose offset comes out zero and whose measurement stamp stays
    where it was put. A payload with an explicit `sent_at` models skew."""
    # Whole seconds, because `parse_ts` truncates to seconds — otherwise the offset would
    # come out as a fraction of a second and break assertions on exact stamp equality.
    arrived_at = (arrived_at or utcnow()).replace(microsecond=0)
    meas = p.get("measurement")
    if isinstance(meas, dict) and "sent_at" not in meas:
        meas["sent_at"] = arrived_at.isoformat()
    return await ingest_one(db, machine_name=machine, payload=p, arrived_at=arrived_at,
                            offset=request_offset(p, arrived_at), is_backlog=is_backlog)


def with_util(five_hour=None, seven_day=None):
    u = json.loads(json.dumps(REAL))
    if five_hour is not None:
        u["five_hour"]["utilization"] = five_hour
    if seven_day is not None:
        u["seven_day"]["utilization"] = seven_day
    return u


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def count(db, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


# ----------------------------------------------------------------------------- basics
async def test_first_measurement_creates_account_series_and_samples(db):
    r = await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    assert r["ok"] and r["samples_written"] > 0

    acc = (await db.execute(select(Account))).scalars().one()
    assert acc.account_uuid == ACCOUNT_MAX["uuid"]
    assert acc.org_type == "claude_max"
    assert acc.subscription_type == "max"

    # 3 non-empty buckets (five_hour, seven_day) + 3 limits + extra_usage + spend
    keys = {s.series_key for s in (await db.execute(select(UsageSeries))).scalars()}
    assert "bucket:five_hour" in keys
    assert "spend:org" in keys, "spend must be a series — on Team it IS the binding limit"
    assert any(k.startswith("limit:weekly_all") for k in keys)


async def test_empty_buckets_create_no_samples(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    keys = {s.series_key for s in (await db.execute(select(UsageSeries))).scalars()}
    assert "bucket:seven_day_opus" not in keys       # null in the real payload


async def test_raw_payload_is_preserved(db):
    r = await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    b = (await db.execute(select(IngestBatch).where(IngestBatch.id == r["batch_id"]))).scalar_one()
    assert b.raw_payload_id is not None and b.payload_sha256


async def test_usage_raw_is_what_lands_in_raw_payloads(db):
    """Rule 6 asks for what Anthropic sent, and `usage` is our own edit of it: `sanitize`
    has already zeroed every boundary it refused by the time the payload is built. From
    probe 15 the untouched cache block travels as `usage_raw`, and THAT is what is stored —
    otherwise `GET /batches/{id}/raw`, documented as "Anthropic's raw response", serves the
    merged object and the value that tripped a guard is unrecoverable."""
    # `payload()` hands out the SHARED `REAL` dict, so this copies before touching it —
    # mutating it in place corrupts every test that runs after this one in the module.
    published = copy.deepcopy(REAL)
    published["five_hour"]["resets_at"] = None           # what sanitize published
    p = payload(usage=published)
    p["usage_raw"] = {"five_hour": {"utilization": 40,
                                    "resets_at": "2099-01-01T00:00:00+00:00"}}
    r = await ingest_one(db, machine_name="desktop", payload=p)
    await db.commit()
    b = (await db.execute(select(IngestBatch).where(IngestBatch.id == r["batch_id"]))).scalar_one()
    rp = (await db.execute(select(RawPayload).where(RawPayload.id == b.raw_payload_id))).scalar_one()
    assert "2099-01-01T00:00:00+00:00" in rp.body, "the refused boundary must survive"


async def test_a_probe_without_usage_raw_still_stores_something(db):
    """The whole fleet is on an older probe and sends no `usage_raw`. For those the merged
    object is not a compromise, it is all there is."""
    r = await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    b = (await db.execute(select(IngestBatch).where(IngestBatch.id == r["batch_id"]))).scalar_one()
    rp = (await db.execute(select(RawPayload).where(RawPayload.id == b.raw_payload_id))).scalar_one()
    assert "five_hour" in rp.body


# --------------------------------------------------------------------------- dedup
# Measurement stamps lie in the PAST, because `measured_at` clamps them to `arrived_at`:
# a measurement cannot be newer than the moment it arrived. Intervals counted from the
# future would collapse to a single point and the test would pass while checking nothing.
def _t0(seconds_ago=600):
    return utcnow().replace(microsecond=0) - timedelta(seconds=seconds_ago)


async def test_dedup_does_not_write_identical_rows(db):
    now = _t0()
    await send(db, payload(captured_at=now))
    after_first = await count(db, LimitSample)
    # the same payload 60 s later — below the heartbeat (300 s)
    await send(db, payload(captured_at=now + timedelta(seconds=60)))
    await db.commit()
    assert await count(db, LimitSample) == after_first, "identical value must not duplicate rows"


async def test_dedup_works_when_ONLY_resets_at_microseconds_changed(db):
    """Regression on a real defect that quietly killed dedup for a full day.

    The test above uses a fixture, so `resets_at` is byte-identical and dedup
    "worked". In the wild Anthropic stamps `resets_at` with the microseconds of ITS
    OWN RESPONSE, so every sample had a different value, the comparison always came out
    "it changed" and every measurement went to the database as a new row. Measured: 63
    samples in 6 h, 63 different `resets_at`, with only 10 distinct utilization values.
    """
    now = _t0()
    u1 = json.loads(json.dumps(REAL))
    u1["five_hour"]["resets_at"] = "2026-07-27T00:59:59.056340+00:00"
    u1["seven_day"]["resets_at"] = "2026-08-01T15:59:59.056361+00:00"
    await send(db, payload(usage=u1, captured_at=now))
    after_first = await count(db, LimitSample)

    # the same window boundary, different response microseconds — NOT a data change
    u2 = json.loads(json.dumps(u1))
    u2["five_hour"]["resets_at"] = "2026-07-27T00:59:59.981119+00:00"
    u2["seven_day"]["resets_at"] = "2026-08-01T15:59:59.998004+00:00"
    await send(db, payload(usage=u2, captured_at=now + timedelta(seconds=60)))
    await db.commit()

    assert await count(db, LimitSample) == after_first, \
        "response microseconds are not a value change and must not create rows"


async def test_monotonicity_guard_works_under_microsecond_noise(db):
    """The same defect disabled the monotonicity guard: it fired only on an UNCHANGED
    `resets_at`, and that changed every time. The effect was worse than a bloated table —
    a stale reading from a second machine could roll `series_state` and the Live view
    back."""
    now = _t0()
    u1 = json.loads(json.dumps(REAL))
    u1["five_hour"]["utilization"] = 60.0
    u1["five_hour"]["resets_at"] = "2026-07-27T00:59:59.111111+00:00"
    await send(db, payload(usage=u1, captured_at=now))

    u2 = json.loads(json.dumps(u1))
    u2["five_hour"]["utilization"] = 40.0      # a drop with no window change => old reading
    u2["five_hour"]["resets_at"] = "2026-07-27T00:59:59.999999+00:00"
    await send(db, payload(usage=u2, captured_at=now + timedelta(seconds=30)),
               machine="laptop")
    await db.commit()

    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "stale_read" in types, "a drop at the same window boundary is a stale read"

    st = (await db.execute(
        select(SeriesState).join(UsageSeries, UsageSeries.id == SeriesState.series_id)
        .where(UsageSeries.series_key == "bucket:five_hour")
    )).scalars().first()
    assert st is not None and float(st.last_utilization) == 60.0, "state must not roll back"


async def test_heartbeat_writes_despite_no_change(db):
    """A gap larger than the heartbeat (300 s) writes a sample even though the value did
    not change.

    Stamps no longer have to fall within any skew tolerance: the server dates a measurement
    from the age reported by the client, so an old stamp is simply an old measurement."""
    now = _t0()
    await send(db, payload(captured_at=now - timedelta(seconds=400)))
    n1 = await count(db, LimitSample)
    await send(db, payload(captured_at=now))
    await db.commit()
    assert await count(db, LimitSample) > n1


async def test_captured_at_in_the_future_is_clamped(db):
    """A payload WITHOUT `sent_at` (a probe below v5): the offset is zero, so the client's
    stamp goes through untouched — except that it cannot be newer than the moment it
    arrived. Previously the server time was substituted; the effect is the same, but only
    by accident, because that same substitution ALSO made stamps 400 s in the past look fresher."""
    arrived = utcnow().replace(microsecond=0)
    await ingest_one(db, machine_name="desktop", arrived_at=arrived,
                     payload=payload(captured_at=arrived + timedelta(seconds=400)))
    await db.commit()
    stamps = {s.captured_at for s in (await db.execute(select(LimitSample))).scalars()}
    assert stamps and max(stamps) <= arrived


async def test_measurement_dated_after_send_is_rejected(db):
    """The client clock moved backwards between recording the measurement and sending it.
    `sent_at - ts` is then negative, i.e. the measurement supposedly came into being AFTER
    the send — the dating is unreliable, and the stamp would land on the request's anchor
    and pass `newest`, overwriting the state. The whole entry is rejected; the raw payload
    goes to the database anyway (rule 6)."""
    arrived = utcnow().replace(microsecond=0)
    r = await send(db, payload(captured_at=arrived + timedelta(seconds=90),
                               sent_at=arrived), arrived_at=arrived)
    await db.commit()
    assert not r["ok"] and r["samples_written"] == 0
    assert await count(db, LimitSample) == 0
    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "clock_backwards" in types
    b = (await db.execute(select(IngestBatch))).scalars().one()
    assert b.raw_payload_id is not None, "raw payload must be saved"


async def test_value_change_always_writes(db):
    now = _t0()
    await send(db, payload(captured_at=now))
    n1 = await count(db, LimitSample)
    await send(db, payload(usage=with_util(five_hour=12.0),
                           captured_at=now + timedelta(seconds=30)))
    await db.commit()
    assert await count(db, LimitSample) == n1 + 1


# --------------------------------------------------------------------------- guard
async def test_stale_read_does_not_roll_back_state(db):
    """Two machines on the same account each have their OWN cache. An older reading from
    machine B must not roll the indicator back — it would look like a window reset."""
    now = _t0()
    await send(db, payload(usage=with_util(five_hour=40.0), captured_at=now))
    await db.commit()

    await send(db, payload(usage=with_util(five_hour=25.0),
                           captured_at=now + timedelta(seconds=30)), machine="laptop")
    await db.commit()

    series = (await db.execute(
        select(UsageSeries).where(UsageSeries.series_key == "bucket:five_hour")
    )).scalar_one()
    st = (await db.execute(
        select(SeriesState).where(SeriesState.series_id == series.id)
    )).scalar_one()
    assert float(st.last_utilization) == 40.0, "current state must not roll back"

    stale = (await db.execute(
        select(LimitSample).where(LimitSample.stale_read.is_(True))
    )).scalars().all()
    assert stale, "a stale read must be saved, only flagged"


async def test_real_drop_with_changed_resets_at_passes(db):
    """A drop with a CHANGED resets_at is a real window reset, not a stale reading."""
    now = _t0()
    await send(db, payload(usage=with_util(five_hour=90.0), captured_at=now))
    u = with_util(five_hour=3.0)
    u["five_hour"]["resets_at"] = "2026-07-27T01:00:00.000000+00:00"
    await send(db, payload(usage=u, captured_at=now + timedelta(seconds=30)))
    await db.commit()

    series = (await db.execute(
        select(UsageSeries).where(UsageSeries.series_key == "bucket:five_hour")
    )).scalar_one()
    st = (await db.execute(
        select(SeriesState).where(SeriesState.series_id == series.id)
    )).scalar_one()
    assert float(st.last_utilization) == 3.0


# ------------------------------------------------------------------------ accounts
async def test_account_switch_is_detected(db):
    await ingest_one(db, machine_name="desktop", payload=payload(account=ACCOUNT_MAX))
    await db.commit()
    await ingest_one(db, machine_name="desktop", payload=payload(account=ACCOUNT_TEAM))
    await db.commit()

    assert await count(db, Account) == 2
    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "account_switched" in types
    assert "new_account_for_token" in types


async def test_team_keeps_seat_tier(db):
    await ingest_one(db, machine_name="desktop", payload=payload(account=ACCOUNT_TEAM))
    await db.commit()
    a = (await db.execute(
        select(Account).where(Account.account_uuid == ACCOUNT_TEAM["uuid"])
    )).scalar_one()
    assert a.org_type == "claude_team" and a.seat_tier == "premium"


async def test_payload_without_account_is_rejected(db):
    r = await ingest_one(db, machine_name="desktop", payload=payload(account={}))
    await db.commit()
    assert not r["ok"] and r["samples_written"] == 0
    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "no_oauth_account" in types


# --------------------------------------------------------------------------- drift
async def test_new_bucket_registers_series_and_reports_it(db):
    u = json.loads(json.dumps(REAL))
    u["brand_new_limit"] = {"utilization": 55.5, "resets_at": "2026-08-02T00:00:00+00:00"}
    r = await ingest_one(db, machine_name="desktop", payload=payload(usage=u))
    await db.commit()
    assert "bucket:brand_new_limit" in r["series_registered"]


async def test_corrupted_field_does_not_interrupt_measurement(db):
    u = json.loads(json.dumps(REAL))
    u["five_hour"] = "suddenly a string"
    r = await ingest_one(db, machine_name="desktop", payload=payload(usage=u))
    await db.commit()
    assert r["ok"] and r["samples_written"] > 0     # the other series got through
    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "schema_drift" in types


async def test_skewed_client_clock_does_not_break_dating(db):
    """The client clock is 5 h behind. The measurement is 120 s old on ITS clock — and must
    be just as old after the write, because age is a difference within one clock while the
    anchor is the server's.

    This inverts the old behavior: on such a skew `resolve_captured_at` substituted the
    server time for the moment of MEASUREMENT, i.e. it claimed the value had been measured
    a moment ago. The event stays — as diagnostics, with no influence on the write."""
    arrived = utcnow().replace(microsecond=0)
    client_clock = arrived - timedelta(hours=5)
    await send(db, payload(captured_at=client_clock - timedelta(seconds=120),
                           sent_at=client_clock), arrived_at=arrived)
    await db.commit()

    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "clock_skew" in types
    stamps = {s.captured_at for s in (await db.execute(select(LimitSample))).scalars()}
    assert stamps == {arrived - timedelta(seconds=120)}

    b = (await db.execute(select(IngestBatch))).scalars().one()
    assert b.clock_offset_s == 5 * 3600, "the offset must be reproducible from the database"


# --------------------------------------------------------------------------- status
async def test_status_shows_account_with_plan_and_active_limit(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    st = await build_status(db)

    assert st.contract_version == 3
    assert len(st.accounts) == 1
    a = st.accounts[0]
    assert a.org_type == "claude_max"
    assert a.rate_limit_tier == "default_claude_max_5x"

    by = {s.series_key: s for s in a.series}
    assert by["bucket:five_hour"].freshness == "live"
    assert by["bucket:five_hour"].utilization == pytest.approx(REAL["five_hour"]["utilization"])

    active = [s for s in a.series if s.is_active]
    assert len(active) == 1
    assert active[0].kind == "weekly_all"

    # Series semantics, without which the UI would have to guess which series is the
    # 5 h window.
    assert by["bucket:five_hour"].bucket_key == "five_hour"
    session_series = [s for s in a.series if s.kind == "session"]
    assert len(session_series) == 1 and session_series[0].group == "session"


async def test_bucket_limit_duplicates_are_detected_from_data(db):
    """The API reports the same limit twice: as a bucket and as an entry in limits[].
    Pairing goes by the data, not by a hardcoded map — the limits[] entry wins,
    because it carries is_active and severity."""
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    st = await build_status(db)
    by = {s.series_key: s for s in st.accounts[0].series}

    assert by["bucket:five_hour"].primary is False
    assert by["bucket:five_hour"].duplicate_of.startswith("limit:session")
    assert by["bucket:seven_day"].primary is False
    # limits[] entries stay primary
    assert all(s.primary for s in st.accounts[0].series if s.source == "limit")
    # spend has no counterpart in limits[] and must stay primary
    assert by["spend:org"].primary is True


async def test_value_divergence_is_not_treated_as_duplicate(db):
    """The reference repo documents that newer responses zero out older per-model fields.
    When the values diverge, both series must stay visible — showing the divergence is
    better than hiding it."""
    u = json.loads(json.dumps(REAL))
    u["five_hour"]["utilization"] = 77.0          # the bucket disagrees with limits[]
    await ingest_one(db, machine_name="desktop", payload=payload(usage=u))
    await db.commit()
    st = await build_status(db)
    by = {s.series_key: s for s in st.accounts[0].series}
    assert by["bucket:five_hour"].primary is True


async def test_status_does_not_show_empty_series(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    st = await build_status(db)
    assert all(s.raw_utilization is not None or s.freshness != "live"
               for s in st.accounts[0].series)


# -------------------------------------------------- freshness vs value change (v3)
async def _five_hour_state(db):
    row = (await db.execute(
        select(SeriesState).join(UsageSeries, UsageSeries.id == SeriesState.series_id)
        .where(UsageSeries.series_key == "bucket:five_hour")
    )).scalar_one()
    return row


async def test_unchanged_value_still_refreshes_confirmation(db):
    """The heart of the matter: dedup writes no sample when the value has not changed. Were
    freshness computed from the time of the last SAMPLE, a stable reading would start to
    look like a broken connection after 5 minutes — even though the client reports every
    60 s."""
    t0 = _t0(600)
    await send(db, payload(captured_at=t0))
    await db.commit()
    st0 = await _five_hour_state(db)
    assert st0.last_captured_at == st0.last_confirmed_at == t0

    # the same value, measured 230 s later — still below the heartbeat threshold (300 s)
    t1 = t0 + timedelta(seconds=230)
    await send(db, payload(captured_at=t1))
    await db.commit()

    st1 = await _five_hour_state(db)
    assert st1.last_captured_at == t0, "sample deliberately NOT written (dedup)"
    assert st1.last_confirmed_at == t1, "but the measurement happened and must be visible"


async def test_freshness_is_computed_from_confirmation_not_from_sample(db):
    """The same scenario seen through /api/status: the series must stay `live`."""
    t0 = utcnow() - timedelta(minutes=8)
    await ingest_one(db, machine_name="desktop", payload=payload(captured_at=t0))
    await db.commit()
    await ingest_one(db, machine_name="desktop",
                     payload=payload(captured_at=utcnow() - timedelta(seconds=30)))
    await db.commit()

    st = await build_status(db)
    five = {s.series_key: s for s in st.accounts[0].series}["bucket:five_hour"]
    assert five.freshness == "live"
    assert five.utilization is not None


async def test_value_since_does_not_move_on_unchanged_value(db, monkeypatch):
    """"Unchanged since" must point at the start of the constant value. Were a heartbeat
    write to move it, the counter would reset on every heartbeat and show an untruth."""
    monkeypatch.setattr(settings, "sample_heartbeat_sec", 60)
    t0 = _t0(600)
    await send(db, payload(captured_at=t0))
    await db.commit()
    # over the heartbeat => the sample WILL be written even though the value is the same
    t1 = t0 + timedelta(seconds=230)
    await send(db, payload(captured_at=t1))
    await db.commit()

    st = await _five_hour_state(db)
    assert st.last_captured_at == t1, "heartbeat wrote a new sample"
    assert st.value_since == t0, "but the value has stayed unchanged since the first measurement"


async def test_value_since_moves_on_value_change(db):
    t0 = _t0(600)
    await send(db, payload(captured_at=t0))
    await db.commit()
    t1 = t0 + timedelta(seconds=120)
    await send(db, payload(usage=with_util(five_hour=91.0), captured_at=t1))
    await db.commit()

    st = await _five_hour_state(db)
    assert float(st.last_utilization) == 91.0
    assert st.value_since == t1


async def test_measurement_source_reaches_sample_and_batch(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    batch = (await db.execute(select(IngestBatch))).scalars().first()
    assert batch.measurement_source == "cli_merged"
    assert batch.cache_age_s == 42 and batch.fresh_age_s == 7
    sample = (await db.execute(select(LimitSample))).scalars().first()
    assert sample.source == "cli_merged"


async def test_payload_without_measurement_lands_as_probe(db):
    """Backward compatibility: spool entries written by probe v2 have no measurement block."""
    p = payload()
    del p["measurement"]
    await ingest_one(db, machine_name="desktop", payload=p)
    await db.commit()
    assert (await db.execute(select(LimitSample))).scalars().first().source == "probe"


async def test_missing_token_meta_does_not_break_write(db):
    """macOS keeps credentials in the Keychain — since probe version 3 the measurement
    does not need them."""
    p = payload()
    del p["token_meta"]
    r = await ingest_one(db, machine_name="mac", payload=p)
    await db.commit()
    assert r["ok"] and r["samples_written"] > 0
    acc = (await db.execute(select(Account))).scalars().one()
    assert acc.subscription_type is None      # no plan tag, but the data is there


async def test_corrected_label_reaches_series_registered_earlier(db):
    """A label is a DESCRIPTION, not an identity — which is why `get_or_create_series`
    refreshes it on every measurement. Without that, a wording fix would never reach series
    already registered and the UI would show the old text for the rest of the database's
    life. The mechanism had no test, and the `spend:org` label change rests on it."""
    from sqlalchemy import update as sa_update

    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    await db.execute(sa_update(UsageSeries)
                     .where(UsageSeries.series_key == "spend:org")
                     .values(display_label="Label from a previous version"))
    await db.commit()

    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()

    s = (await db.execute(
        select(UsageSeries).where(UsageSeries.series_key == "spend:org")
    )).scalar_one()
    assert s.display_label == "Spend limit (your pool)"
