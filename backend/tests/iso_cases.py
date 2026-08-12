"""One table of ISO-8601 forms, read by BOTH date parsers in this project.

NOT `test_*.py`, so pytest does not collect it; the tests import `tests.iso_cases`.

There are two parsers and they cannot become one:

  * `client/usage-probe.py::_epoch` -> epoch as a float, KEEPING the fraction, because the
    signaller orders `since` (one-second resolution) against transcript timestamps
    (milliseconds), and a truncated comparison inverts the order.
  * `backend/app/parsing.py::parse_ts` -> naive UTC datetime TRUNCATED to whole seconds,
    because the database columns are naive and the microseconds of `resets_at` are the
    stamp of Anthropic's own response, not of the window boundary (rule 9's wobble).

Nor may they be shared as code: the probe ships as a SINGLE file (the enroll skill copies
one file, the hook command names one path, the redirect `runpy`s one file), so it can
import nothing local.

What is left is agreement by test. Each row states the instant a form denotes and which
side is expected to read it. **The asymmetries are rows in the table, not omissions** —
that is the whole point of the file: a change on either side that widens or narrows what it
accepts turns into a red test instead of a discovery three years later.

`_epoch` grew this table after a defect that lived exactly in the gap it now covers: the
probe held TWO `_epoch` definitions, Python bound the second, and `sanitize` ran a parser
that read `iso[:19]` as UTC and discarded the offset. Every observed `resets_at` carries
`+00:00`, where both bodies agreed, so 25 tests stayed green over it.
"""
from datetime import datetime, timezone

# A form is read by a side, refused by it (-> None), or the answer depends on the
# interpreter. The last one is not a hedge: `datetime.fromisoformat` accepts an offset
# without a colon only from Python 3.11, and the probe runs on whatever `python3` the
# machine has — 3.9.13 on the dev box, 3.13.5 on the fleet host.
READS = "reads"
REFUSES = "refuses"
DEPENDS = "depends-on-interpreter"


class Case:
    def __init__(self, label, raw, instant, probe, backend, why=""):
        self.label = label
        self.raw = raw
        self.instant = instant          # the moment it denotes, aware UTC, or None
        self.probe = probe
        self.backend = backend
        self.why = why

    def __repr__(self):
        return "<%s %r>" % (self.label, self.raw)


def _utc(y, mo, d, h, mi, s, us=0):
    return datetime(y, mo, d, h, mi, s, us, tzinfo=timezone.utc)


CASES = [
    # ---- both sides read these; this is every form the two producers actually emit ----
    Case("offset +00:00, microseconds", "2026-07-27T10:29:59.761469+00:00",
         _utc(2026, 7, 27, 10, 29, 59, 761469), READS, READS,
         "`resets_at` as Anthropic writes it — 23331 of 23331 observed"),
    Case("offset +02:00", "2026-07-27T10:29:59.761469+02:00",
         _utc(2026, 7, 27, 8, 29, 59, 761469), READS, READS,
         "the offset must MOVE the instant; the shadowing parser read it as +00:00"),
    Case("offset -05:00", "2026-07-27T10:29:59.761469-05:00",
         _utc(2026, 7, 27, 15, 29, 59, 761469), READS, READS,
         "the other direction, for the same reason"),
    Case("Z, milliseconds", "2026-08-11T13:53:28.588Z",
         _utc(2026, 8, 11, 13, 53, 28, 588000), READS, READS,
         "transcript timestamps — 457 of 457 observed; fromisoformat rejects Z below 3.11"),
    Case("Z, whole seconds", "2026-08-11T13:53:28Z",
         _utc(2026, 8, 11, 13, 53, 28), READS, READS,
         "`since`, written by the probe's own `_iso()`"),
    Case("naive, no zone", "2026-07-27T10:29:59",
         _utc(2026, 7, 27, 10, 29, 59), READS, READS,
         "MUST be read as UTC, never as local time — .timestamp() on a naive datetime "
         "applies the machine's zone and would put `since` and the transcript hours apart"),
    Case("more than 6 fractional digits", "2026-07-27T10:29:59.7614695555+05:30",
         _utc(2026, 7, 27, 4, 59, 59, 761469), READS, READS,
         "fromisoformat takes at most 6; both sides truncate before calling it"),
    Case("one fractional digit", "2026-07-27T10:29:59.1+00:00",
         _utc(2026, 7, 27, 10, 29, 59, 100000), READS, READS,
         "below 3.11 fromisoformat takes EXACTLY 3 or 6 digits, so 1, 2, 4 and 5 raise and "
         "the probe's `except` turns that into None — which SKIPS sanitize's expiry guard. "
         "Hence padding, not just truncation. Both pass on 3.13 whatever the code does; "
         "only a 3.9 run exercises them"),
    Case("five fractional digits", "2026-07-27T10:29:59.12345+00:00",
         _utc(2026, 7, 27, 10, 29, 59, 123450), READS, READS,
         "the far edge of the same gap. No producer emits either width (isoformat 0 or 6, "
         "toISOString 3, `_iso()` none) — these guard a format change, not a seen value"),
    Case("surrounding whitespace", "  2026-07-27T10:29:59+00:00  ",
         _utc(2026, 7, 27, 10, 29, 59), READS, READS,
         "both strip"),

    # ---- the backend reads these, the probe does not. A recorded asymmetry ----
    Case("date only", "2026-07-27",
         _utc(2026, 7, 27, 0, 0, 0), REFUSES, READS,
         "10 chars — the probe wants at least 19, so it declines rather than guessing a "
         "midnight nobody sent. The value still reaches the backend verbatim in `usage_raw`"),
    Case("epoch seconds, numeric", 1785148199,
         _utc(2026, 7, 27, 10, 29, 59), REFUSES, READS,
         "`parse_ts` takes it because the statusline emits epochs; the probe takes str only"),
    Case("epoch milliseconds, numeric", 1785148199000,
         _utc(2026, 7, 27, 10, 29, 59), REFUSES, READS,
         "`parse_ts` has a >1e12 branch for it"),

    # ---- interpreter-dependent on the probe side ----
    Case("offset without a colon", "2026-07-27T10:29:59+0200",
         _utc(2026, 7, 27, 8, 29, 59), DEPENDS, READS,
         "fromisoformat accepts the ISO-basic offset only from 3.11. What matters is that "
         "the probe never answers with a WRONG instant — None or the right one, never +00:00"),

    # ---- neither reads these ----
    Case("not a date at all", "nope", None, REFUSES, REFUSES, ""),
    Case("empty", "", None, REFUSES, REFUSES, ""),
    Case("None", None, None, REFUSES, REFUSES, ""),
]
