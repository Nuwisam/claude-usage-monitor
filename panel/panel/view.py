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
from . import fmt, log, model as M


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
    for test in (lambda s: M.panel_kind(s.kind) == M.AGGREGATE_WEEKLY,
                 lambda s: s.bucket_key == "seven_day"):
        for s in primary:
            if test(s):
                return s
    for s in series:
        if s.bucket_key == "seven_day":
            return s
    return None


#: Kinds already reported, so an unknown word is said ONCE per process rather than once
#: per frame -- `pick_scoped` runs on every repaint. Log de-duplication and nothing else:
#: no decision reads this set, so `pick_scoped` stays the pure function `band_state` and
#: `tools/replay.py` need -- the same series in the same frame picks the same rung whether
#: or not the line was written.
_reported_kinds = set()


def _looks_weekly(s):
    """Whether a series is plainly a weekly window, whatever its kind is called.

    Read off the two fields a RENAMED KIND would not touch: the group the backend files it
    under ("weekly") and the bucket it came from ("seven_day", "seven_day_fable"). Both are
    weaker than the kind, which is why they only ever raise a question here and never pick
    the rung.
    """
    return s.group == "weekly" or (s.bucket_key or "").startswith("seven_day")


def _report_unknown_kind(s):
    """Says out loud that a weekly window arrived under a word the panel does not know.

    The failure this closes is a SILENT one: the filter in `pick_scoped` yields an empty
    list, which is indistinguishable from "this account has no scoped window", and the band
    draws a smaller, entirely plausible shape -- no exception, no log line, nothing on the
    glass, and the panel's own tests keep passing because they carry their own copy of the
    literal.

    A line in the log is the WHOLE remedy, deliberately: raising would take the panel down
    over a word, and AGENTS.md rule 5 says a new bucket at Anthropic has to work with no
    change here. The panel keeps drawing what it understood; the log says what it did not.
    """
    if s.kind in _reported_kinds:
        return
    _reported_kinds.add(s.kind)
    log.get().warning(
        "unknown weekly series kind %r (%s) -- no scoped rung will be drawn for it; "
        "map it onto a panel word in panel/panel/model.py::KINDS", s.kind, s.series_key)


def pick_scoped(series, keep=None):
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

    `keep` filters the CANDIDATES, before the ranking rather than after it. WHY the caller
    drops a series (freshness, today) is not this function's business; WHEN it is applied
    is. A series the caller would discard must not be able to win the comparison first and
    take the rung down with it -- rejecting the winner afterwards leaves an account whose
    scoped window is live drawing no scoped rung at all, because a withdrawn series
    outranked it. That is only reachable with two of them, which no account here has yet.

    The kind is matched through the panel's OWN word (`model.SCOPED_WEEKLY`), and upstream's
    string is mapped onto it in one place (`model.KINDS`). A kind that maps to nothing, on a
    series that is plainly a weekly window, is REPORTED rather than dropped in silence --
    that is the difference between a renamed kind and an account without a scoped window,
    and the filter alone cannot tell them apart.
    """
    scoped = []
    for s in series:
        word = M.panel_kind(s.kind)
        if word == M.SCOPED_WEEKLY:
            scoped.append(s)
        elif word is None and s.kind and _looks_weekly(s):
            # A series with NO kind at all is not asked about: the bucket-sourced series
            # carry none by design (backend/app/parsing.py), and an absent word is not a
            # renamed one.
            _report_unknown_kind(s)
    if keep is not None:
        scoped = [s for s in scoped if keep(s)]
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


def resets_aligned(a, b, now=None):
    """Whether two windows close at the same moment, and so can share one countdown.

    A missing boundary counts as aligned, and this is where the panel's question parts
    company with the backend's. `same_reset_window` calls a null on ONE side a change,
    because it is asking "has anything moved". This asks "can one countdown speak for
    both", and a window with no boundary has nothing to contradict: Anthropic gives none
    for a window at 0 % usage, which is 3288 of the 3290 readings with no scoped boundary
    here. The history says the same the other way -- all nine times the scoped window
    gained a boundary out of nothing, it was the account's own weekly one.

    A SPENT boundary is a third case, and it is NOT the same as a missing one. Given `now`,
    a boundary already behind us belongs to a window that has closed. The two windows do
    not roll over in the same reading -- each series is confirmed from its own
    (backend/app/services/ingest.py) -- so for the span of a probe interval one side can
    hold next week's instant while the other still holds last week's.

    Both spent: aligned. Neither window has a live deadline, so the shared row has no
    countdown it could get wrong -- the null-vs-null case in another guise.

    Exactly one spent: NOT aligned. Treating the spent side as merely absent would glue the
    row, and a glued row carries ONE countdown, which is then a whole WEEK wrong for
    whichever track it does not describe. No caption saves it: the live deadline lies about
    the rolled-over track, and "reset has passed" lies about a track with a week still to
    run. A pair that cannot be captioned honestly is not glued, so the two come apart and
    each carries its own -- which is also what keeps `render._pair`'s caption picker
    complete, since a spent week can then only reach it when the scoped side is spent too
    and both captions say the same thing.

    That does mean the band reshapes for the span of a probe interval at every rollover.
    That transient is the truth about the data at that moment -- for those seconds the two
    windows really do have different deadlines -- so the reshape is honest and suppressing
    it was the error.

    Spent-ness is decided by `_boundary_ahead`, on the same rounded second the captions
    use, so one instant cannot answer here and in `reset_note` two different ways.

    Without `now` the test is exactly what it was, which keeps every existing caller and
    test honest.
    """
    a_live = _boundary_ahead(a, now)
    b_live = _boundary_ahead(b, now)
    if a is not None and b is not None:
        a_spent, b_spent = a_live is None, b_live is None
        if a_spent and b_spent:
            return True
        if a_spent != b_spent:
            return False
    if a_live is None or b_live is None:
        return True
    return abs((a_live - b_live).total_seconds()) <= RESET_ALIGN_TOLERANCE_S


def _boundary_ahead(t, now):
    """`t`, or None when it has already been spent. No `now` means no opinion.

    Spent on the WHOLE SECOND -- the threshold `reset_note` and `fmt.countdown`
    already share -- because both answers reach the SAME frame (render.py builds
    `reset_week`/`reset_scoped` and `reset_aligned` from one `now_ms`). On an
    exact `t <= now` a boundary 400 ms away is still "ahead" here while the
    caption beside it already reads "reset has passed": one instant, two
    answers, for half a second at every rollover. That is the splice the comment
    in `reset_note` went out of its way to close, closed the same way.
    """
    if t is None or now is None:
        return t
    return None if int(round((t - now).total_seconds())) <= 0 else t


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
