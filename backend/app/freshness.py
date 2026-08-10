"""Four freshness states — a pure function, testable as a table.

THE MOST IMPORTANT RULE IN THE WHOLE PROJECT:
    The tool has to be able to say "I don't know" instead of guessing.

The worst failure mode is showing a false, CONFIDENT-looking zero — because a big job
gets launched on that basis and runs into a wall. So we tell "the window reset and there
was no activity" (we may infer 0%) apart from "the client is reporting, but there is no
data for this series" (a failure — 0% must not be shown).

The distinction rests on `last_batch_at`, that is, on whether the client spoke up at all.
Without that information the two cases look identical.
"""
from __future__ import annotations

from datetime import datetime

LIVE = "live"                       # a fresh sample
STALE = "stale"                     # older, but still true (utilization does not rise by itself)
INFERRED_RESET = "inferred_reset"   # the window reset, no activity => ~0%
UNKNOWN = "unknown"                 # we do not know; NEVER render as 0%


def freshness(
    now: datetime,
    confirmed_at: datetime | None,
    resets_at: datetime | None,
    last_batch_at: datetime | None,
    fresh_window_sec: int = 300,
    client_silent_sec: int = 21600,
) -> str:
    """Age is counted from the CONFIRMATION, not from the sample write.

    Dedup deliberately writes no sample when the value has not changed — so the time of the
    last SAMPLE can be several minutes older than the last real measurement. Counting the age
    from it would show a stable reading as expired, that is, it would confuse "nothing is
    changing" with "we have lost contact". Those are two completely different things for
    someone who is deciding right now whether to launch a big job."""
    if confirmed_at is None:
        return UNKNOWN

    age = (now - confirmed_at).total_seconds()
    if age <= fresh_window_sec:
        return LIVE

    if resets_at is not None and now > resets_at:
        # The window rolled over after our last sample.
        if last_batch_at is not None and last_batch_at > resets_at:
            # The client was ALIVE after the reset, and still there is no sample for
            # this series. That is not silence — that is a failure. Zero must not be inferred.
            return UNKNOWN
        # The client was silent through the whole reset => no activity => the window really is empty.
        return INFERRED_RESET

    # The window is still running. The value stays true, because utilization does not rise by itself.
    # If the client has been silent for suspiciously long, we would rather admit we do not know.
    if last_batch_at is None or (now - last_batch_at).total_seconds() > client_silent_sec:
        return UNKNOWN
    return STALE


def display_utilization(state: str, last_utilization: float | None) -> float | None:
    """What to show. On INFERRED_RESET we return 0.0, but the caller MUST mark it
    visually as inference, not as a measurement. On UNKNOWN we return None — and that
    is the only correct answer."""
    if state == INFERRED_RESET:
        return 0.0
    if state == UNKNOWN:
        return None
    return last_utilization
