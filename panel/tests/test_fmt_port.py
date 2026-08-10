"""fmt.py has to give EXACTLY the same strings as frontend/src/lib/time.ts and format.ts.

It is the same product watched from two screens — a divergence in the time format
would be visible at once and would look like a data bug, not a format bug.
Every case carries its place in the original in a comment.
"""
from datetime import timedelta

import pytest

from panel import fmt


@pytest.mark.parametrize("secs,want", [
    (2 * 86400 + 4 * 3600, "2 d 4 h"),      # time.ts:60
    (3 * 3600 + 5 * 60, "3 h 05 min"),      # time.ts:61 — leading zero in the minutes
    (12 * 60 + 34, "12 min 34 s"),          # time.ts:62 — leading zero in the seconds
    (3600, "1 h 00 min"),
    (59, "0 min 59 s"),
    (0, "past reset"),                      # time.ts:56
    (-5, "past reset"),
])
def test_countdown(secs, want):
    assert fmt.countdown(secs * 1000.0, 0.0) == want


def test_countdown_without_a_target():
    # time.ts:54 — no boundary is NOT the same thing as a boundary in the past.
    assert fmt.countdown(None, 0.0) == "no reset"


@pytest.mark.parametrize("junk", [1769459260, [], object()])
def test_parse_utc_does_not_blow_up_on_a_non_string(junk):
    """The only entry point for raw server timestamps into the client. A broken frame
    must not kill the panel, and `re.search` on a number raises TypeError, which would
    pass through the whole loop up to the excepthook — one badly serialized field would
    blank the screen for good."""
    assert fmt.parse_utc(junk) is None


@pytest.mark.parametrize("secs,want", [
    (0, "0 s ago"),
    (3, "3 s ago"),                         # time.ts:68
    (59, "59 s ago"),
    (60, "1 min ago"),                      # time.ts:70
    (5 * 60, "5 min ago"),
    (3600 + 25 * 60, "1 h 25 min ago"),     # time.ts:157
    (-10, "0 s ago"),                       # a negative age is clipped to zero
    # The day rung (time.ts:158). The boundary exactly at 24 h gives "1 d 0 h ago",
    # just as countdown() prints "1 d 0 h" for the same input.
    (86400, "1 d 0 h ago"),
    (64 * 3600 + 11 * 60, "2 d 16 h ago"),  # the panel used to write "64 h 11 min ago"
    (3 * 86400 + 4 * 3600, "3 d 4 h ago"),  # the canonical string from AGENTS.md
])
def test_ago(secs, want):
    assert fmt.ago(0.0, secs * 1000.0) == want


@pytest.mark.parametrize("value,want", [
    (31, "31"),                             # format.ts:8 — integers without a tail
    (100.0, "100"),
    (0, "0"),
    (30.5, "30.5"),                         # dot decimal separator, not a comma
    (None, None),                           # None STAYS None — the word is the view's call
])
def test_pct(value, want):
    assert fmt.pct(value) == want


@pytest.mark.parametrize("args,want", [
    ((3820, "USD", 2), "38.20 USD"),        # format.ts:13-23
    ((9000, "USD", 2), "90.00 USD"),
    ((5, "USD", 2), "0.05 USD"),            # cents without losing the leading zero
    ((0, "USD", 2), "0.00 USD"),
    ((3820, None, 2), "38.20"),
    ((3820, "USD", 0), "3820 USD"),         # exponent 0 = no fractional part
    ((-150, "USD", 2), "-1.50 USD"),
    ((None, "USD", 2), None),
])
def test_money(args, want):
    assert fmt.money(*args) == want


def test_money_does_not_go_through_float():
    """The backend keeps amounts in minor units exactly so as not to lose a cent
    (schemas.py). Flattening to a float on the way would waste that effort —
    0.1+0.2 is not 0.3."""
    assert fmt.money(2 ** 53 + 1, "USD", 2).startswith("90071992547409")


@pytest.mark.parametrize("value,want", [
    (None, 0.0), (-5, 0.0), (0, 0.0), (42, 42.0), (100, 100.0), (250, 100.0),
])
def test_clamp_pct(value, want):
    # format.ts:46 — the bar must not run off the track or dip below zero.
    assert fmt.clamp_pct(value) == want


def test_parse_utc_without_a_zone_assumes_utc():
    # time.ts:9-10 — new Date("...") with no zone is LOCAL time in JS, so we append
    # the Z. The same trap on the Python side.
    assert fmt.parse_utc("2026-07-26T18:00:00").utcoffset().total_seconds() == 0
    assert fmt.parse_utc("2026-07-26T18:00:00Z") == fmt.parse_utc("2026-07-26T18:00:00")
    assert fmt.parse_utc(None) is None
    assert fmt.parse_utc("not a date") is None


def test_hours_are_local():
    """time.ts:31 uses getHours(), that is the browser's zone. The panel does the same:
    'reset at 20:00' has to agree with the watch on a wrist, not with UTC."""
    d = fmt.parse_utc("2026-07-26T18:00:00Z")
    assert fmt.hm(d) == fmt.hm(fmt.to_local(d))
    assert fmt.hm(None) == "—"


_NOW = fmt.parse_utc("2026-07-26T12:00:00Z")     # Sunday, noon UTC


def _at(days):
    """Stamp of an instant `days` days away from _NOW, read relative to _NOW."""
    return fmt.at_stamp(_NOW + timedelta(days=days), fmt.ms(_NOW))


def test_at_stamp_has_the_same_rungs_as_the_web():
    """Port of atStamp() (time.ts:94-106). Tz-agnostically: we check the SHAPE of the
    string, because the hour itself depends on the machine's zone.

    The bare hour of a reset five days out lies — it does not say which day."""
    assert _at(0).startswith("at "), "today: the bare hour with a preposition"
    assert _at(-1).startswith("yesterday at ")
    assert _at(1).startswith("tomorrow at ")
    for days in (-6, -2, 2, 6):
        first, second = _at(days).split()[:2]
        assert first == "on", "the preposition is INSIDE the stamp"
        assert second in fmt.DAYS, "the day abbreviation exactly as in the web"
    # 7 days out is the same abbreviation again, so from there on it is dates; a numeric
    # date takes no preposition, so there is none there.
    far = _at(30)
    assert "." in far.split()[0] and " at " in far
    assert not far.startswith("on ")


def test_at_stamp_has_on_before_the_weekday():
    # The weekday branch carries the preposition; the date branch does not.
    assert _at(2).startswith("on Tue. at ")
    assert _at(3).startswith("on Wed. at ")
    assert not _at(30).startswith("on ")


def test_at_stamp_different_year_has_a_year():
    assert _at(-400).split()[0].count(".") == 2


def test_at_stamp_without_a_date():
    assert fmt.at_stamp(None, fmt.ms(_NOW)) == "—"


def test_server_clock_runs_monotonically():
    """Anchor on time.monotonic(), not on the system clock: the panel runs for months
    and an NTP jump must not shift the countdowns."""
    t = [100.0]
    clock = fmt.ServerClock(lambda: t[0])
    assert not clock.anchored
    assert clock.anchor("2026-07-26T19:07:40Z")
    start = clock.now_ms()
    t[0] += 5.0
    assert clock.now_ms() - start == pytest.approx(5000.0)
    assert clock.anchored


def test_server_clock_without_an_anchor_does_not_blow_up():
    clock = fmt.ServerClock(lambda: 0.0)
    assert clock.now_ms() > 0
