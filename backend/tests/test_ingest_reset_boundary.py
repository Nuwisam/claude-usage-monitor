"""An unknown window boundary versus the current state (F1 + F2).

One defect in two places.

  * F2 ARMED IT: `st.last_resets_at = o.resets_at` was unconditional, so a measurement with
    no boundary erased a known boundary.
  * F1 FIRED IT: the monotonicity guard asked "the same window?" with the SAME bool dedup
    uses to ask "did anything change at all". For dedup, "no boundary on either side"
    correctly means "no change" (series without a boundary rest on that). For the guard it
    means "unknown" — and not knowing, taken as proof, froze the state: a drop 92% -> 0%
    went to the database with the `stale_read` flag and `series_state` stood still.

Observed live (account 1, 2026-07-30): five samples 11:51:05-11:55:13 UTC with
`stale_read=1`, the state frozen at 92% against real usage of 0-2%; released at 11:56:15,
when Anthropic supplied the new boundary 16:49:59. Reproducible on EVERY session window
reset, because for a fresh window with 0% usage Anthropic gives no boundary. For `spend:org`
and `extra:usage` the boundary NEVER arrives, so the freeze was permanent there.

Timestamps are kept in the PAST, because a measurement cannot be newer than the moment it
arrived — `measured_at` clamps it to the request's anchor and intervals counted from the
future would collapse to a single point. Microseconds are zeroed, because `parse_ts`
truncates to whole seconds.
"""
import json
from datetime import timedelta

from sqlalchemy import func, select

from app.models import IngestEvent, LimitSample, SeriesState, UsageSeries
from app.services.ingest import ingest_one
from app.services.status import build_status
from tests.test_ingest_e2e import REAL, db, payload, utcnow   # noqa: F401


def five_hour(util, resets_at):
    """The real payload with the value and boundary of the `five_hour` bucket swapped in.

    `resets_at=None` reproduces EXACTLY what the probe does under the `reset-in-progress` rule:
    the percentage stays (it is fresh and true), only the boundary field itself is zeroed,
    because it comes from a stale cache (`client/usage-probe.py`, `sanitize`)."""
    u = json.loads(json.dumps(REAL))
    u["five_hour"]["utilization"] = util
    u["five_hour"]["resets_at"] = None if resets_at is None else resets_at.isoformat()
    return u


def spend(percent):
    """The `spend:org` series — on a Team account this IS the binding limit, and it NEVER
    has a boundary."""
    u = json.loads(json.dumps(REAL))
    u["spend"]["percent"] = percent
    return u


async def state(db, series_key) -> SeriesState:
    return (await db.execute(
        select(SeriesState).join(UsageSeries, UsageSeries.id == SeriesState.series_id)
        .where(UsageSeries.series_key == series_key)
    )).scalars().one()


async def samples(db, series_key) -> int:
    return (await db.execute(
        select(func.count()).select_from(LimitSample)
        .join(UsageSeries, UsageSeries.id == LimitSample.series_id)
        .where(UsageSeries.series_key == series_key)
    )).scalar_one()


async def stale_flags(db, series_key) -> list[bool]:
    return list((await db.execute(
        select(LimitSample.stale_read)
        .join(UsageSeries, UsageSeries.id == LimitSample.series_id)
        .where(UsageSeries.series_key == series_key)
    )).scalars().all())


async def events(db) -> set[str]:
    return {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}


async def series_in_status(db, series_key):
    st = await build_status(db)
    return {s.series_key: s for s in st.accounts[0].series}[series_key]


# ------------------------------------------------------------------------ F1: no proof
async def test_reset_without_boundary_does_not_freeze_state(db):
    """A replay of the live incident, three steps and all three are needed.

    t0  the boundary is known and usage is 92% — a normal window in progress;
    t1  the boundary has passed, the probe zeroed it, the percentage is still fresh (this
        ARMED F1);
    t2  the window rolled over, usage 0%, Anthropic still gives no boundary (this FIRED it).

    Before the fix the state stayed at 92% for 9.5 minutes of real time, that is for as long
    as Anthropic said nothing about the new boundary.
    """
    t0 = (utcnow() - timedelta(seconds=240)).replace(microsecond=0)
    boundary = t0 + timedelta(seconds=30)          # passes between t0 and t1
    t1, t2 = t0 + timedelta(seconds=60), t0 + timedelta(seconds=120)

    for ts, u in ((t0, five_hour(92.0, boundary)),
                  (t1, five_hour(92.0, None)),
                  (t2, five_hour(0.0, None))):
        await ingest_one(db, machine_name="desktop", payload=payload(usage=u, captured_at=ts))
        await db.commit()

    st = await state(db, "bucket:five_hour")
    assert float(st.last_utilization) == 0.0, \
        "no boundary on either side is not proof that it is still the same window"
    assert st.last_captured_at == t2
    assert "stale_read" not in await events(db)

    # The same seen through /api/status, i.e. the symptom the operator reported:
    # "limit usage at the ceiling" next to "reset time unknown".
    assert (await series_in_status(db, "bucket:five_hour")).utilization == 0.0


async def test_series_without_boundary_can_drop(db):
    """`spend:org` and `extra:usage` NEVER have a boundary — for them the freeze was PERMANENT.
    The first true drop (a monthly settlement, a credits top-up) would have stopped the state
    forever, because the boundary that releases it never arrives."""
    t0 = (utcnow() - timedelta(seconds=180)).replace(microsecond=0)
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=spend(60), captured_at=t0))
    await db.commit()
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=spend(5), captured_at=t0 + timedelta(seconds=60)))
    await db.commit()

    assert float((await state(db, "spend:org")).last_utilization) == 5.0, \
        "a series without a boundary must not freeze forever"
    # The state alone is not everything: a sample with `stale_read` also drops out of the
    # history and out of the delta baseline (services/status.py, `_recent_samples` filters
    # on that flag).
    assert not any(await stale_flags(db, "spend:org"))


async def test_guard_still_freezes_when_both_boundaries_are_known(db):
    """The F1 fix has no right to disable the guard. The simplest "fix" — never fire it —
    strips away the only protection against a machine that holds an older token cache while
    its reading only looks fresh (its own `captured_at`, so `newest` will not stop it).

    The boundary on the laptop's side differs by a second: that is the WOBBLE, not another
    window (rule 9)."""
    t0 = (utcnow() - timedelta(seconds=180)).replace(microsecond=0)
    boundary = t0 + timedelta(hours=3)
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=five_hour(60.0, boundary), captured_at=t0))
    await db.commit()
    await ingest_one(db, machine_name="laptop",
                     payload=payload(usage=five_hour(40.0, boundary + timedelta(seconds=1)),
                                     captured_at=t0 + timedelta(seconds=30)))
    await db.commit()

    assert float((await state(db, "bucket:five_hour")).last_utilization) == 60.0, \
        "a drop at a PROVABLY identical window is a stale read, state does not roll back"
    assert "stale_read" in await events(db)


async def test_no_boundary_on_either_side_still_deduplicates(db):
    """The line the fix must not cross: `same_reset_window(None, None) is True` is CORRECT
    for dedup. Had the guard been fixed by changing this function's answer (instead of adding
    a separate question about proof), `spend:org` and `extra:usage` would get a row on EVERY
    probe — and the probe polls every ~60 s."""
    t0 = (utcnow() - timedelta(seconds=240)).replace(microsecond=0)
    for i in range(3):
        await ingest_one(db, machine_name="desktop",
                         payload=payload(usage=spend(60),
                                         captured_at=t0 + timedelta(seconds=60 * i)))
        await db.commit()

    assert await samples(db, "spend:org") == 1, \
        "a series without a boundary at an unchanged value is one row, not three"


# -------------------------------------------------- F2: what has to keep the state
async def test_a_still_valid_boundary_survives_a_measurement_without_one(db):
    """A measurement with no boundary does not erase a boundary that HAS NOT PASSED YET —
    that one still describes the window in progress, and it feeds the countdown,
    `secondsToReset`, clamping the delta to the window and the `inferred_reset` inference.

    The value MUST change. With an unchanged one, dedup writes no sample, the state block
    does not execute at all and the boundary would stay in the state of its own accord — the
    test would pass vacuously, whatever the guarded line does."""
    t0 = (utcnow() - timedelta(seconds=180)).replace(microsecond=0)
    boundary = t0 + timedelta(hours=3)
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=five_hour(40.0, boundary), captured_at=t0))
    await db.commit()
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=five_hour(45.0, None),
                                     captured_at=t0 + timedelta(seconds=60)))
    await db.commit()

    st = await state(db, "bucket:five_hour")
    assert float(st.last_utilization) == 45.0, "the state block MUST have executed"
    assert st.last_resets_at == boundary, "a boundary in the future describes the ONGOING window"

    # The operator's symptom from the other side: `resetNote()` in the UI says "reset time
    # unknown" exactly when `resetsAt` is null with non-zero usage.
    series = await series_in_status(db, "bucket:five_hour")
    assert series.resets_at == boundary and series.seconds_to_reset > 0


async def test_expired_boundary_does_not_stay_in_state(db):
    """The other side of the same trade-off, and the line that must not be crossed.

    ONLY a boundary that has not passed yet is carried forward. A boundary that has passed
    says nothing about the current window — keeping it would give a confident-looking untruth
    ("the reset passed at 20:00" against 95% usage in a window that ends 5 hours later), that
    is exactly what rule 4 forbids. Visible ignorance beats a confident wrong number."""
    t0 = (utcnow() - timedelta(seconds=180)).replace(microsecond=0)
    boundary = t0 + timedelta(seconds=30)          # passes before the second measurement
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=five_hour(92.0, boundary), captured_at=t0))
    await db.commit()
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=five_hour(95.0, None),
                                     captured_at=t0 + timedelta(seconds=60)))
    await db.commit()

    st = await state(db, "bucket:five_hour")
    assert float(st.last_utilization) == 95.0, "the state block MUST have executed"
    assert st.last_resets_at is None, "an expired boundary does not describe the current window"

    series = await series_in_status(db, "bucket:five_hour")
    assert series.resets_at is None and series.seconds_to_reset is None


async def test_measurement_without_boundary_does_not_break_dedup(db):
    """Dedup asks "will the STATE CHANGE". Were it comparing against the raw `o.resets_at`,
    then with a carried-forward boundary every subsequent measurement without a boundary would
    look like a change (a known boundary in the state vs NULL in the measurement) and would
    write a row every ~60 s — the F2 fix would break the mechanism it has to coexist with."""
    t0 = (utcnow() - timedelta(seconds=240)).replace(microsecond=0)
    boundary = t0 + timedelta(hours=3)
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=five_hour(40.0, boundary), captured_at=t0))
    await db.commit()
    after_first = await samples(db, "bucket:five_hour")
    assert after_first == 1

    for i in (1, 2):
        await ingest_one(db, machine_name="desktop",
                         payload=payload(usage=five_hour(40.0, None),
                                         captured_at=t0 + timedelta(seconds=60 * i)))
        await db.commit()

    assert await samples(db, "bucket:five_hour") == after_first, \
        "an unchanged value with a carried-forward boundary is still no change"
