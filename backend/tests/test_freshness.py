"""Table-driven tests of the four freshness states.

The most important test in the project is `test_a_live_client_with_no_samples_gives_unknown` —
it checks that the tool will NOT show a false zero when the probe breaks down.
"""
from datetime import datetime, timedelta

import pytest

from app.freshness import (
    INFERRED_RESET, LIVE, STALE, UNKNOWN, display_utilization, freshness,
)

NOW = datetime(2026, 7, 26, 20, 0, 0)


def m(minutes):
    return NOW - timedelta(minutes=minutes)


def test_fresh_sample():
    assert freshness(NOW, m(1), NOW + timedelta(hours=2), m(1)) == LIVE


def test_an_older_sample_in_an_ongoing_window_is_still_valid():
    """utilization does not rise by itself, so a value from an hour ago is still
    correct as long as the window has not reset."""
    assert freshness(NOW, m(60), NOW + timedelta(hours=1), m(60)) == STALE


def test_after_reset_with_no_activity_zero_is_inferred():
    """The window has passed, the client was silent the whole time => nobody burned anything."""
    reset = NOW - timedelta(hours=2)
    assert freshness(NOW, m(240), reset, m(240)) == INFERRED_RESET


def test_a_live_client_with_no_samples_gives_unknown():
    """THE MOST IMPORTANT CASE.

    The window reset, but the client was ALIVE after the reset and still there is no sample
    for this series. That is a probe failure, not an absence of activity. Were we to infer
    0%, the user would launch a big job believing the limit is full — and hit a wall.
    """
    reset = NOW - timedelta(hours=2)
    last_batch = NOW - timedelta(minutes=5)      # the client is alive
    assert freshness(NOW, m(240), reset, last_batch) == UNKNOWN


def test_long_client_silence_in_an_ongoing_window_gives_unknown():
    assert freshness(NOW, m(600), NOW + timedelta(hours=1), m(600),
                     client_silent_sec=3600) == UNKNOWN


def test_no_sample_gives_unknown():
    assert freshness(NOW, None, None, None) == UNKNOWN


def test_missing_resets_at_does_not_crash():
    assert freshness(NOW, m(2), None, m(2)) == LIVE
    assert freshness(NOW, m(60), None, m(60)) == STALE


def test_freshness_window_boundary():
    assert freshness(NOW, NOW - timedelta(seconds=300), None, NOW, fresh_window_sec=300) == LIVE
    assert freshness(NOW, NOW - timedelta(seconds=301), None, NOW, fresh_window_sec=300) == STALE


@pytest.mark.parametrize("state,last,expected", [
    (LIVE, 42.0, 42.0),
    (STALE, 42.0, 42.0),
    (INFERRED_RESET, 42.0, 0.0),      # inference, to be marked visually
    (UNKNOWN, 42.0, None),            # the only correct answer is no number
])
def test_display_utilization(state, last, expected):
    assert display_utilization(state, last) == expected


def test_unknown_never_returns_zero():
    """Regression on the most dangerous bug: UNKNOWN must not pretend to be 0%."""
    assert display_utilization(UNKNOWN, 0.0) is None
    assert display_utilization(UNKNOWN, 99.9) is None
