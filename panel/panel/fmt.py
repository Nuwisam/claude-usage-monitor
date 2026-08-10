"""Formats — a port of frontend/src/lib/time.ts and format.ts.

The strings MUST come out identical to the web, because it is the same product
watched from two screens at once. The tests in tests/test_fmt_port.py hold it to that.

Time: countdowns are computed from the SERVER clock (anchor `serverNow`), but the
hours shown are in the LOCAL zone — exactly like `time.ts:hm`, which uses
`d.getHours()`. That way "reset at 20:00" agrees with the watch on a wrist.

Seconds stay where the mockup puts them: in a countdown below an hour and in a
reading's age below a minute. They cost a full frame every second (the panel cannot
refresh a fragment), but they come in exactly when work is happening — and that is
when they are needed most. Outside work the values climb into minutes and hours on
their own and the panel goes quiet. The only exception is the clock in the header:
it ticks regardless of work, so it shows HH:MM.

From time.ts we do not port `stamp()` (the stamp WITHOUT a preposition): the panel
has no caller for it, because it has no "unchanged since" or "since" labels. Should
such a label appear, `stamp()` is the twin of `at_stamp()` from time.ts:71-83.
"""
import re
from datetime import datetime, timezone

_HAS_ZONE = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")

# Day abbreviations EXACTLY as in time.ts:44 — indexed from SUNDAY, like getDay().
# Python's weekday() counts from Monday, so the indexing is done by _day_index(), not by
# a reordered table: the strings have to come out identical to the web.
DAYS = ("Sun.", "Mon.", "Tue.", "Wed.", "Thu.", "Fri.", "Sat.")


def parse_utc(iso):
    """ISO -> datetime with tzinfo. With no zone we append UTC, like parseUtc in time.ts.

    A non-string returns None, not an exception. This is the only entry point for raw
    server timestamps into the whole client (series stamps, `resetsAt`, `serverNow`), and
    the rule here is hard: a broken frame must not kill the panel. `re.search` on a number
    raises TypeError, which would pass through tick() and run() all the way to the
    excepthook — so one field badly serialized by the backend would blank the screen.
    """
    if not iso or not isinstance(iso, str):
        return None
    text = iso if _HAS_ZONE.search(iso) else iso + "Z"
    text = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def to_local(d):
    return d.astimezone() if d is not None else None


def ms(d):
    """datetime -> epoch milliseconds, so the arithmetic runs as it does in JS."""
    return None if d is None else d.timestamp() * 1000.0


def _p2(n):
    return "%02d" % n


def hm(d):
    d = to_local(d)
    return "%s:%s" % (_p2(d.hour), _p2(d.minute)) if d else "—"


def hms(d):
    d = to_local(d)
    return "%s:%s:%s" % (_p2(d.hour), _p2(d.minute), _p2(d.second)) if d else "—"


def dm(d):
    d = to_local(d)
    return "%s.%s" % (_p2(d.day), _p2(d.month)) if d else "—"


def _day_index(d):
    """Index into DAYS in the JS getDay() convention: 0 is Sunday."""
    return (d.weekday() + 1) % 7


def _day_diff(d, now):
    """Difference in CALENDAR DAYS across local midnights (time.ts:51-55).

    Never delta_ms / 86_400_000: a day at a clock change has 23 or 25 h, and a pair of
    instants on two sides of midnight differ by a day no matter how many ms separate
    them. A person reads "yesterday at 23:50", not "26 hours ago".
    """
    return (to_local(d).date() - to_local(now).date()).days


def at_stamp(d, now_ms):
    """Stamp of an instant read RELATIVE TO NOW, with a preposition — a port of atStamp().

        today         ->  "at 11:58"
        +/- 1 day     ->  "yesterday at 23:50" / "tomorrow at 20:00"
        +/- 2..6 days ->  "on Wed. at 11:58"
        further       ->  "26.07 at 11:58"   (a numeric date takes no preposition)
        other year    ->  "26.07.2025 at 11:58"

    The preposition is INSIDE the stamp, because the format is what decides it, and the
    caller has no right to know which variant came out.

    The `precise` parameter from time.ts:94 is not ported: the seconds variant lights up
    only in the "confirmed …" label in the web hero, which the panel does not have. The
    same criterion by which the `outline` field fell out of SeriesView — no reader, no
    field.
    """
    if d is None:
        return "—"
    now = datetime.fromtimestamp(now_ms / 1000.0, tz=timezone.utc)
    diff = _day_diff(d, now)
    if diff == 0:
        return "at %s" % hm(d)
    if diff == -1:
        return "yesterday at %s" % hm(d)
    if diff == 1:
        return "tomorrow at %s" % hm(d)
    local = to_local(d)
    if abs(diff) <= 6:
        return "on %s at %s" % (DAYS[_day_index(local)], hm(d))
    year = "" if local.year == to_local(now).year else ".%d" % local.year
    return "%s%s at %s" % (dm(d), year, hm(d))


def countdown(target_ms, now_ms):
    """"2 d 4 h" / "3 h 05 min" / "12 min 34 s" / "past reset" / "no reset"."""
    if target_ms is None:
        return "no reset"
    s = int(round((target_ms - now_ms) / 1000.0))
    if s <= 0:
        return "past reset"
    d, rest = divmod(s, 86400)
    h, rest = divmod(rest, 3600)
    m, sec = divmod(rest, 60)
    if d > 0:
        return "%d d %d h" % (d, h)
    if h > 0:
        return "%d h %s min" % (h, _p2(m))
    return "%d min %s s" % (m, _p2(sec))


def ago(since_ms, now_ms):
    """"3 s ago" / "5 min ago" / "1 h 25 min ago" / "3 d 4 h ago".

    A day rung shaped like countdown(), because ever since freshness is carried by the
    label itself, three days of silence has to read at once — "76 h 00 min ago" needs
    division in the head. The boundary exactly at 24 h gives "1 d 0 h ago";
    countdown() prints "1 d 0 h" for the same input, so that is consistent.
    """
    if since_ms is None:
        return "—"
    s = max(0, int(round((now_ms - since_ms) / 1000.0)))
    if s < 60:
        return "%d s ago" % s
    m = s // 60
    if m < 60:
        return "%d min ago" % m
    h = m // 60
    if h < 24:
        return "%d h %s min ago" % (h, _p2(m % 60))
    return "%d d %d h ago" % (h // 24, h % 24)


def waited(since_ms, now_ms):
    """How long Claude is waiting: "a moment" / "4 min" / "1 h 05 min" / "2 d 3 h".

    COARSE, and that is not a matter of taste. The AX206 cannot do partial updates, so every
    change of a string on the card is a full frame and 355 ms on USB — and the second panel
    costs its own. Seconds would turn ~2.5% of the link load into ~35% for the whole life of
    the card. So below a minute there is no number, only a word: there is nothing to count
    there anyway.

    The objection "the clock in the banner ticks every second anyway" does not apply: the
    card has no live clock, the hour in the banner is the static moment the prompt appeared.

    It differs from `ago()` by the missing "ago": this is a duration, not a stamp.
    """
    if since_ms is None:
        return "—"
    s = max(0, int(round((now_ms - since_ms) / 1000.0)))
    if s < 60:
        return "a moment"
    m = s // 60
    if m < 60:
        return "%d min" % m
    h = m // 60
    if h < 24:
        return "%d h %s min" % (h, _p2(m % 60))
    return "%d d %d h" % (h // 24, h % 24)


def pct(v):
    """31 -> "31", 30.5 -> "30.5". None stays None — what to show instead of the
    number is the view's decision, because in the `unknown` state the answer is a word."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:                      # NaN
        return None
    if f == int(f):
        return str(int(f))
    return "%.1f" % f


def money(minor, currency=None, exponent=2):
    """(3820, "USD", 2) -> "38.20 USD".

    Computed on INTEGERS. The backend never flattens amounts to a float
    (schemas.py: "in minor units with an exponent, never as a float")
    and there is no reason to do it on the way to the screen.
    """
    if minor is None:
        return None
    exp = 2 if exponent is None else int(exponent)
    sign = "-" if minor < 0 else ""
    whole, frac = divmod(abs(int(minor)), 10 ** exp) if exp > 0 else (abs(int(minor)), 0)
    text = "%s%d" % (sign, whole)
    if exp > 0:
        text += "." + str(frac).rjust(exp, "0")
    return "%s %s" % (text, currency) if currency else text


def clamp_pct(v):
    """The bar must not run off the track or dip below zero."""
    if v is None:
        return 0.0
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    if f != f:
        return 0.0
    return max(0.0, min(100.0, f))


class ServerClock:
    """Server clock anchored on a monotonic one.

    time.ts uses Date.now(), because the browser has nothing better. The panel runs for
    months, so we anchor on time.monotonic() — an NTP jump or a clock change will not
    shift the countdowns. The semantics visible from the outside are the same.
    """

    def __init__(self, monotonic):
        self._monotonic = monotonic
        self._server_ms = None
        self._anchor = None

    def anchor(self, server_now_iso):
        d = parse_utc(server_now_iso)
        if d is None:
            return False
        self._server_ms = d.timestamp() * 1000.0
        self._anchor = self._monotonic()
        return True

    @property
    def anchored(self):
        return self._server_ms is not None

    def now_ms(self):
        if self._server_ms is None:
            return datetime.now(timezone.utc).timestamp() * 1000.0
        return self._server_ms + (self._monotonic() - self._anchor) * 1000.0

    def now(self):
        return datetime.fromtimestamp(self.now_ms() / 1000.0, tz=timezone.utc)
