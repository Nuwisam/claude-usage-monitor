"""Reset boundaries — derived from the samples, not from the bucket.

The regression this covers: `resets[]` was filled in only in the `bucket="raw"` branch,
while `bucket=auto` over 24 h picks `5m`. The effect: the default history view had not one
reset boundary — the most important line was missing from the chart of a resetting window.
"""
from datetime import datetime, timedelta

from app.routers.read import _auto_bucket, _reset_boundaries

T0 = datetime(2026, 7, 26, 12, 0, 0)
R1 = datetime(2026, 7, 26, 15, 0, 0)
R2 = datetime(2026, 7, 26, 20, 0, 0)
R3 = datetime(2026, 7, 27, 1, 0, 0)


def at(mins):
    return T0 + timedelta(minutes=mins)


def test_a_change_in_resets_at_gives_a_boundary_at_the_moment_it_was_observed():
    rows = [(at(0), R1), (at(5), R1), (at(10), R2), (at(15), R2), (at(20), R3)]
    assert _reset_boundaries(rows) == [at(10), at(20)]


def test_the_first_sample_is_not_a_reset():
    """The first `resets_at` is only a reference point — no window came up at that moment."""
    assert _reset_boundaries([(at(0), R1), (at(5), R1)]) == []


def test_samples_without_resets_at_are_skipped_without_losing_the_reference_point():
    # spend:org has no resets_at; such a series has no boundaries and must not fake them
    assert _reset_boundaries([(at(0), None), (at(5), None)]) == []
    # and a mixture does not lose the previous value: a None in the middle is no new window
    assert _reset_boundaries([(at(0), R1), (at(5), None), (at(10), R1)]) == []


def test_empty_history_has_no_boundaries():
    assert _reset_boundaries([]) == []


def test_window_boundary_jitter_is_not_a_reset():
    """The boundary Anthropic reports wobbles within ONE window. A real measurement:
    49 samples over 3 h gave values from `00:59:59.014384` to `01:00:00.982268` — under
    2 seconds of spread, crossing a minute boundary.

    On a literal comparison the history drew a reset boundary at EVERY sample:
    61 "resets" a day for a window that resets 5 times."""
    base = datetime(2026, 7, 27, 0, 59, 59, 14384)
    rows = [(at(i), base + timedelta(milliseconds=i * 100)) for i in range(20)]
    assert _reset_boundaries(rows) == []

    # ...but a genuine jump of the window (5 h) still yields exactly one boundary
    rows.append((at(25), base + timedelta(hours=5)))
    assert _reset_boundaries(rows) == [at(25)]


def test_slow_drift_does_not_accumulate_into_a_false_reset():
    """The reference point moves with every sample, so a drift of 1 s per sample across a
    whole day still will not cross the threshold — otherwise after a few hours a fictional
    boundary would arise out of nothing but accumulated noise."""
    base = datetime(2026, 7, 27, 0, 59, 59)
    rows = [(at(i), base + timedelta(seconds=i)) for i in range(400)]
    assert _reset_boundaries(rows) == []


def test_auto_picks_an_aggregated_bucket_for_the_default_24h():
    """Proof that the regression was real: the default range is NOT 'raw'."""
    assert _auto_bucket(24 * 3600) == "5m"
    assert _auto_bucket(6 * 3600) == "raw"
    assert _auto_bucket(30 * 24 * 3600) == "1h"
