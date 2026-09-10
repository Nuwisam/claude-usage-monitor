"""State -> appearance. A port of frontend/src/lib/freshness.ts for the panel.

NOT A DIFFERENCE FROM THE WEB, though this file claimed one for a long time: the
panel has no separate freshness states in the drawing, and neither has the web.
Recency is carried on both sides by the reading-age label ("16 min ago") sitting
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
    # `--probe` test card depends on them (panel/README.md).
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
    """The hero is permanently anchored to the 5 h session. Were it to follow `isActive`,
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

    A scoped series (Fable, Opus and so on) never lands here, even when it is the
    higher of the two: this row has to mean the same thing on every day of the week.
    That is a division of labour, not a suppression -- `pick_scoped` gives the scoped
    window a rung of its own, where it says what it is.
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


def pick_scoped(series):
    """The weekly window of ONE model -- Fable today, whatever ships next tomorrow.

    Picked by its KIND, never by a name: a new model has to appear on the panel by
    itself, the way `plan_label` lets a new tier appear (docs/API.md section 5, "Three
    hard rules of presentation", rule 1 -- the plan is visible next to every account, and
    it is read off the data rather than off a list of tiers this build happens to know).
    Hard-coding "Fable" would mean shipping the panel again for each one.

    An account can carry several. The highest wins, because this rung exists to answer
    "what stops me first"; the loser is a diagnostic and diagnostics stay in the web UI.

    The comparison is TOTAL -- ties broken on the series key -- because two series at
    equal utilization must resolve to the same one on every call. `Surface.plan` sends
    nothing when the packed frame is byte-identical to the last one (`surface.dirty_tiles`
    finds no dirty tile), so a coin-toss here would repaint the screen over USB for
    nothing. Not `Frame` -- that only memoises rotations and packing, and compares nothing.
    """
    scoped = [s for s in series if s.kind == "weekly_scoped"]
    primary = [s for s in scoped if s.primary]
    for group in (primary, scoped):
        if group:
            return max(group, key=lambda s: (
                s.utilization if s.utilization is not None
                else (s.raw_utilization if s.raw_utilization is not None else -1),
                s.series_key or ""))
    return None


#: How far apart two weekly boundaries may stand and still count as ONE boundary.
#:
#: MEASURED for this question, and deliberately NOT borrowed from the backend.
#:
#: Across every reading this deployment holds -- 2026-07-26 to 2026-09-09, two accounts,
#: 909 samples where both windows had a boundary -- the largest gap between the aggregate
#: week and the scoped one is ONE SECOND, and 88 % agree exactly. That second is Anthropic
#: reporting the same instant as "16:00:00" from one series and "15:59:59" from the other.
#: Five seconds clears it several times over while staying far below anything a reader
#: could see.
#:
#: `backend/app/parsing.py::same_reset_window` answers a question that LOOKS like this one
#: and is not: "has anything moved", where the unit of change is a whole window and its
#: 300 s default is two orders of magnitude below the shortest of them. This asks whether
#: ONE COUNTDOWN MAY SPEAK FOR BOTH, and the countdown is drawn in minutes and seconds
#: near a boundary. Borrowing 300 s here was measured wrong: two windows 250 s apart --
#: well inside a seven-day window, nowhere near a rollover -- glued into one row under a
#: single caption that was four minutes wrong for one of its two tracks.
#:
#: The backend's number is also only its DEFAULT (`Field(300, alias="RESET_WINDOW_EPS_SEC")`
#: in `backend/app/config.py`), overridable per deployment, which the panel cannot read. So
#: "the same number as the backend" was never a property anything could hold, and a test
#: pinning the two declared defaults measured agreement the runtime never promised.
RESET_ALIGN_TOLERANCE_S = 5


def resets_aligned(a, b):
    """Whether two windows close at the same moment, and so can share one countdown.

    A missing boundary counts as aligned, and this is where the panel's question parts
    company with the backend's. `same_reset_window` calls a null on ONE side a change,
    because it is asking "has anything moved". This asks "can one countdown speak for
    both", and a window with no boundary has nothing to contradict: Anthropic gives none
    for a window at 0 % usage, which is 3288 of the 3290 readings with no scoped boundary
    here. The history says the same the other way -- all nine times the scoped window
    gained a boundary out of nothing, it was the account's own weekly one.
    """
    if a is None or b is None:
        return True
    return abs((a - b).total_seconds()) <= RESET_ALIGN_TOLERANCE_S


def scoped_label(s):
    """"FABLE" -- the model's own name, taken from the data rather than a dictionary.

    Three places carry it, in falling order of directness: the field, then the label the
    backend composes ("Week — Fable", em dash, built in backend/app/parsing.py), then the
    series key ("limit:weekly_scoped|weekly|fable|-"). A backend that changes the label's
    shape degrades to the key rather than to a wrong word.

    The label is taken apart the way it was put together -- first separator, trailing
    surface -- because the model name is upstream text and may contain either character.
    """
    if s is not None:
        if getattr(s, "model_display_name", None):
            return s.model_display_name.upper()
        label = s.label or ""
        for dash in ("—", "–", " - "):
            if dash in label:
                # Split on the FIRST separator and strip only a TRAILING surface, because
                # that is the order the backend composed them in: the kind label, then
                # " — " + model, then " / " + surface. Taking the last dash or the first
                # slash instead reads a model name that contains one as though it were a
                # separator -- "Week — Sonnet — preview" gave "PREVIEW", and
                # "Week — Sonnet (Bedrock/Vertex)" gave "SONNET (BEDROCK".
                name = label.split(dash, 1)[1]
                # " / " with its spaces, as the backend writes it. A bare "/" inside a
                # model name has none, so it survives.
                return name.rsplit(" / ", 1)[0].strip().upper()
        parts = (s.series_key or "").split("|")
        if len(parts) > 2 and parts[2] not in ("", "-"):
            return parts[2].upper()
    return "WEEKLY"


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
