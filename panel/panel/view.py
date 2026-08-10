"""State -> appearance. A port of frontend/src/lib/freshness.ts for the panel.

NOT A DIFFERENCE FROM THE WEB, though this file claimed one for a long time: the
panel has no separate freshness states in the drawing, and neither has the web.
Recency is carried on both sides by the reading-age label ("16 min ago") standing
next to the account. `freshness.ts` says so in its own header and `describeSeries()`
returns no field that tells `live` from `stale`, so the claim that the web colors the
fill was false in both languages. The port and its original agree.

THE REAL DIFFERENCE is narrower and runs the other way: `freshness.ts` says "no meter"
when `unavailableReason` is set, while `missing_view()` below can only say "unknown",
because `panel/panel/model.py` carries no such field. On the glass a withdrawn meter
and a series that was never measured look the same.

In practice: when a LAST MEASUREMENT exists (`rawUtilization`), the panel shows it
like any other value, and the reading age next to it says how much it is worth.

Rule 4 from AGENTS.md ("unknown is never zero") is thereby kept more strongly, not
more weakly: with no fresh measurement we show the LAST MEASURED value, not zero.
A false, confident-looking zero is the worst failure mode of this tool. The dashed
track and the words `unknown` stay only for the case in which a measurement has
NEVER been taken — there really is nothing to draw.
"""
from . import fmt


class SeriesView:
    # `full` has no drawing of its own — at 100% the full track says so by itself
    # (draw.bar) — but it stays: test_hundred_is_the_end pins it and it carries the
    # only threshold in this UI. There is no `outline` field here, because NOBODY
    # read it: not draw.bar, not render, not a single test. `stub`/`ghost`/
    # `ghost_pct` stay even though the running panel never lights them — the
    # `--probe` test card stands on them (panel/README.md).
    __slots__ = ("measured", "hatch", "stub", "ghost", "ghost_pct",
                 "bar_pct", "full", "number", "words")

    def __init__(self, **kw):
        for k in self.__slots__:
            setattr(self, k, kw.get(k))


def describe_series(s):
    """SeriesStatus -> SeriesView.

    The value comes from `utilization`, and when the backend did not supply it
    (`unknown` state) — from `rawUtilization`, that is from the last MEASUREMENT.
    Whether that number is still worth anything is told by the reading age next
    to it, not by a separate track drawing.
    """
    if s is None:
        return missing_view()

    value = s.utilization
    if value is None:
        value = s.raw_utilization

    if value is None:
        # There has NEVER been a measurement for this series — the only case in
        # which there really is nothing to draw.
        return missing_view()

    return SeriesView(
        measured=True,
        hatch=False,
        # The tilde on an inferred reset stays: it is the only place where the
        # number comes not from a measurement but from the server's reasoning.
        stub=False,
        ghost=False,
        ghost_pct=0.0,
        bar_pct=fmt.clamp_pct(value),
        full=value >= 100,
        number=("~0" if s.freshness == "inferred_reset" and not value
                else fmt.pct(value)),
        words=None,
    )


def missing_view():
    """NO measurement at all: no frame arrived or the series never had a value.
    An empty track would read as zero, so the track is dashed with hatching and
    words stand in place of the number. Zero would be a lie."""
    return SeriesView(measured=False, hatch=True, stub=False,
                      ghost=False, ghost_pct=0.0, bar_pct=0.0, full=False,
                      number=None, words="unknown")


def reset_note(s, now_ms):
    """(lead, at) — the countdown caption. A port of resetNote() from freshness.ts.

    `resetsAt: null` has SEVERAL reasons, and merging them into one caption was
    a bug once already: Anthropic gives no boundary for a window at 0% usage, and
    the probe zeroes a stale boundary from the cache. Four different sentences,
    not one.

    Whether to add the day is decided by `at_stamp` — one place for all windows,
    just as in time.ts. The preposition is INSIDE the stamp ("at 20:00", but
    "on Fri. at 20:00"), so the caller (render.py) only glues "%s · %s" and does
    not add an "at" of its own.
    """
    if s is None:
        return "no data", None
    if s.source in ("spend", "extra_usage"):
        return "no reset", None
    r = fmt.parse_utc(s.resets_at)
    if r is not None:
        target = fmt.ms(r)
        # Rounding threshold THE SAME as in countdown(), otherwise a 300 ms
        # difference produces the splice "reset in past reset".
        secs = int(round((target - now_ms) / 1000.0))
        if secs > 0:
            return ("reset in %s" % fmt.countdown(target, now_ms),
                    fmt.at_stamp(r, now_ms))
        return "reset has passed", fmt.at_stamp(r, now_ms)
    if s.utilization == 0:
        return "window has not started", None
    return "reset time unknown", None


def pick_session(series):
    """The hero stands on the 5 h session PERMANENTLY. Were it to follow `isActive`,
    the same screen would mean something else depending on the day of the week
    (HeroSession.tsx)."""
    primary = [s for s in series if s.primary]
    for test in (lambda s: s.kind == "session",
                 lambda s: s.bucket_key == "five_hour"):
        for s in primary:
            if test(s):
                return s
    for s in series:
        if s.bucket_key == "five_hour":
            return s
    return None


def pick_weekly(series):
    """The weekly row = the AGGREGATE series.

    We deliberately do not show scoped series here (Opus and so on), even though
    a scoped one is sometimes higher. The reason: the row has to mean the same
    thing on every day of the week. Diagnostics stay in the web UI.
    """
    primary = [s for s in series if s.primary]
    for test in (lambda s: s.kind == "weekly_all",
                 lambda s: s.bucket_key == "seven_day"):
        for s in primary:
            if test(s):
                return s
    for s in series:
        if s.bucket_key == "seven_day":
            return s
    return None


class CreditsView:
    __slots__ = ("state", "used", "limit", "currency", "bar_pct", "is_current")

    def __init__(self, state, used=None, limit=None, currency=None,
                 bar_pct=0.0, is_current=False):
        self.state = state          # "on" | "off" | "unknown"
        self.used = used
        self.limit = limit
        self.currency = currency
        self.bar_pct = bar_pct
        self.is_current = is_current


def credits(rung):
    """The `credits` rung of the cascade -> the credits row.

    Three states, because "off" and "unknown" are two different things. With "off"
    and NO AMOUNTS layout 4a does not draw this row at all — the band then has three
    rows.

    Amounts, however, take precedence over the state. When the organization cuts
    credits off, the rung is `off`, but the backend still reports `usedMinor`/
    `limitMinor` from the last MEASUREMENT — the only thing that makes sense on
    480x320: "300.04 / 300.00 EUR". The panel does not write the reason for the
    withdrawal, because there is neither room nor need for it; explanations are the
    web UI's job. A vanishing row would be a real loss instead — the band would drop
    a number it showed before.
    """
    if rung is None or rung.state == "unknown" or rung.state is None:
        return CreditsView("unknown")
    # With `off` we draw ONLY a complete pair of amounts. An account with no credits
    # also has `usedMinor: 0` (Anthropic reports a zeroed `spend.used` there), and
    # "0.00 / —" is a row without content — on 480x320 it costs room and says nothing.
    if rung.state == "off" and not (rung.used_minor is not None and rung.limit_minor):
        return CreditsView("off")
    bar = 0.0
    if rung.used_minor is not None and rung.limit_minor:
        bar = fmt.clamp_pct(rung.used_minor / float(rung.limit_minor) * 100.0)
    return CreditsView(
        rung.state,
        used=fmt.money(rung.used_minor, None, rung.exponent),
        limit=fmt.money(rung.limit_minor, None, rung.exponent),
        currency=rung.currency,
        bar_pct=bar,
        is_current=rung.is_current,
    )


def plan_label(account):
    """"Max 5×" / "Team Standard" — the plan MUST be visible.

    "40%" means something else on Max 20x than on a Team Standard seat, so
    docs/API.md makes it presentation rule 1. We derive it from the token, not from
    a dictionary — a new tier has to show up by itself, even if raw (rule 5).
    """
    if account is None:
        return ""
    token = (account.rate_limit_tier or account.subscription_type
             or account.org_type or "")
    if not token:
        return ""
    text = token.lower()
    for prefix in ("default_", "claude_"):
        text = text.replace(prefix, "")
    words = []
    for word in text.replace("-", "_").split("_"):
        if not word:
            continue
        if len(word) > 1 and word[-1] == "x" and word[:-1].isdigit():
            words.append(word[:-1] + "×")
        else:
            words.append(word[:1].upper() + word[1:])
    return " ".join(words)
