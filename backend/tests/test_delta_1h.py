"""The 1 h delta does not cross a reset boundary.

Regression: right after a session reset the hero read "−46 pp in the last hour", because
the reference point was a sample from the PREVIOUS window — and it stayed that way for a
whole hour, precisely when usage was really climbing from zero.
"""
from datetime import datetime, timedelta

from tests.test_ingest_e2e import (  # noqa: F401 — the `db` fixture comes along with these
    db, ingest_one, payload, utcnow, with_util,
)

from app.services.status import _delta_1h, build_status

NOW = datetime(2026, 7, 27, 12, 0, 0)
B_NEXT = NOW + timedelta(hours=4)      # the current window's boundary
B_PREV = NOW - timedelta(hours=1)      # a boundary that has already passed


def trace(*items):
    """(minutes ago, utilization, boundary) -> rows, ascending by time."""
    return [(NOW - timedelta(minutes=m), u, r) for m, u, r in items]


def test_no_current_value_means_no_delta():
    assert _delta_1h(trace((30, 10.0, B_NEXT)), now=NOW, current=None,
                     resets_at=B_NEXT) == (None, None)


def test_after_the_boundary_has_passed_there_is_no_delta():
    """All the samples belong to the previous window — the same condition on which
    freshness() declares inferred_reset. Without this the result was exactly "−46 pp"."""
    rows = trace((50, 46.0, B_PREV), (20, 46.0, B_PREV))
    assert _delta_1h(rows, now=NOW, current=0.0, resets_at=B_PREV) == (None, None)


def test_one_sample_in_the_window_is_not_a_spread():
    rows = trace((60, 46.0, B_PREV), (2, 3.0, B_NEXT))
    assert _delta_1h(rows, now=NOW, current=3.0, resets_at=B_NEXT) == (None, None)


def test_baseline_clipped_to_the_current_window():
    rows = trace((60, 46.0, B_PREV), (30, 46.0, B_PREV),
                    (5, 2.0, B_NEXT), (1, 4.0, B_NEXT))
    d, t0 = _delta_1h(rows, now=NOW, current=4.0, resets_at=B_NEXT)
    assert d == 2.0, "delta computed from the first sample AFTER the reset"
    assert d != -42.0, "baseline from the previous window — exactly this bug"
    assert t0 == NOW - timedelta(minutes=5)


def test_a_full_hour_within_one_window():
    rows = trace((58, 12.0, B_NEXT), (30, 20.0, B_NEXT), (1, 31.0, B_NEXT))
    d, t0 = _delta_1h(rows, now=NOW, current=31.0, resets_at=B_NEXT)
    assert d == 19.0
    assert (NOW - t0).total_seconds() >= 45 * 60, 'UI will label this as "within the hour"'


def test_reset_in_progress_when_the_boundary_has_been_zeroed():
    """The probe zeroes out a stale boundary, so `resets_at` exposes the reset neither in
    the samples nor in the series state — what remains is the sample date and the drop."""
    rows = trace((50, 46.0, B_PREV), (4, 0.0, None), (1, 3.0, None))
    d, t0 = _delta_1h(rows, now=NOW, current=3.0, resets_at=None)
    assert (d, t0) == (3.0, NOW - timedelta(minutes=4))


def test_no_samples_means_no_delta():
    assert _delta_1h([], now=NOW, current=5.0, resets_at=B_NEXT) == (None, None)


def test_rounding_to_four_decimal_places():
    rows = trace((30, 1.0, B_NEXT), (1, 4.123456, B_NEXT))
    d, _ = _delta_1h(rows, now=NOW, current=4.123456, resets_at=B_NEXT)
    assert d == 3.1235


# --------------------------------------------------------------------------- e2e
async def test_status_after_reset_does_not_show_a_negative_delta(db, monkeypatch):
    """The same path a live run takes: ingest -> series_state -> /api/status."""
    import app.services.ingest as ing
    import app.services.status as stat

    t0 = utcnow().replace(microsecond=0)     # samples are truncated to seconds anyway

    async def ingest_at(minutes, five_hour, resets_at):
        when = t0 + timedelta(minutes=minutes)
        monkeypatch.setattr(ing, "utcnow", lambda: when)
        u = with_util(five_hour=five_hour)
        u["five_hour"]["resets_at"] = resets_at.isoformat() + "Z"
        for lim in u["limits"]:
            if lim.get("kind") == "session":
                lim["percent"] = five_hour
                lim["resets_at"] = resets_at.isoformat() + "Z"
        await ingest_one(db, machine_name="desktop",
                         payload=payload(usage=u, captured_at=when))

    # explicit boundaries: the fixture's are long past, and the probe drops an expired window
    await ingest_at(0, 50.0, t0 + timedelta(minutes=20))
    await ingest_at(25, 2.0, t0 + timedelta(hours=5, minutes=20))
    await ingest_at(35, 5.0, t0 + timedelta(hours=5, minutes=20))
    await db.commit()

    monkeypatch.setattr(stat, "utcnow", lambda: t0 + timedelta(minutes=36))
    st = await build_status(db)
    sessions = [s for s in st.accounts[0].series if s.bucket_key == "five_hour"]
    assert sessions, "scenario did not produce a session series — the test would be empty"

    for s in sessions:
        assert s.delta_pct_1h == 3.0, "delta computed from the first sample after the reset"
        assert s.delta_pct_1h != -45.0, "baseline from before the reset — regression"
        assert s.delta_from == t0 + timedelta(minutes=25)
