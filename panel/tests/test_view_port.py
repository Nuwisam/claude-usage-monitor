"""view.py — state to appearance. Here we guard rule 4 from AGENTS.md.

The most important test in this file is `test_unknown_shows_last_measurement_not_zero`. A false,
confident-looking zero is the worst failure mode of this tool: the user fires off a
big job and hits a wall.
"""
import pytest

from panel import fmt, model, view

NOW = fmt.ms(fmt.parse_utc("2026-07-26T19:07:40Z"))


def series(**kw):
    d = {"seriesKey": "limit:session|session|-|-", "label": "Session",
         "source": "limit", "kind": "session", "bucketKey": "five_hour",
         "primary": True, "freshness": "live", "sortOrder": 15}
    d.update(kw)
    return model.SeriesStatus(d)


# --- rule 4 -----------------------------------------------------------------

def test_unknown_shows_last_measurement_not_zero():
    """Rule 4 in its panel form: when the backend does not know the current value
    we show the LAST MEASURED one, never zero. How much that number is worth is
    told by the reading age next to it — the panel has no separate freshness
    states in the drawing."""
    v = view.describe_series(series(freshness="unknown", utilization=None,
                                    rawUtilization=42))
    assert v.number == "42", "the last measurement, not a word and not zero"
    assert v.bar_pct == 42 and v.measured is True
    assert v.words is None


def test_unknown_at_hundred_does_not_lose_the_hundred():
    """A real case: an account goes silent for 12 h, the week was at 100%. Showing
    zero or an empty track would be the worst possible lie here."""
    v = view.describe_series(series(freshness="unknown", utilization=None,
                                    rawUtilization=100))
    assert v.number == "100" and v.full is True and v.bar_pct == 100


def test_no_measurement_at_all_gives_words_not_zero():
    """The only case in which there really is nothing to draw."""
    for v in (view.missing_view(),
              view.describe_series(series(freshness="unknown", utilization=None,
                                          rawUtilization=None)),
              view.describe_series(None)):
        assert v.number is None and v.words == "unknown"
        assert v.bar_pct == 0 and v.measured is False and v.hatch is True


def test_inferred_reset_has_a_tilde():
    """The tilde tells inference apart from measurement (freshness.ts:84) — the
    only place where the number comes not from a measurement but from the
    server's reasoning."""
    v = view.describe_series(series(freshness="inferred_reset", utilization=0,
                                    rawUtilization=97))
    assert v.number == "~0" and v.bar_pct == 0


@pytest.mark.parametrize("freshness", ["live", "stale"])
def test_live_and_stale_look_the_same(freshness):
    """A DELIBERATE departure from the web: the reading age stands next to it and
    says the same thing more precisely. A second channel for the same information
    would be noise."""
    v = view.describe_series(series(freshness=freshness, utilization=31))
    assert v.measured is True and v.number == "31" and v.hatch is False


def test_hundred_is_the_end():
    # The only threshold in the whole UI. 100% is not "almost the end" — it is the
    # end of the rung.
    assert view.describe_series(series(utilization=100)).full is True
    assert view.describe_series(series(utilization=99)).full is False


# --- reset captions ---------------------------------------------------------

def test_four_reasons_for_missing_boundary_give_four_captions():
    """resetsAt: null has several causes and merging them into one caption was a bug
    once already (freshness.ts:105-125). The count is asserted separately, because
    a set with a duplicate dedupes in silence: two captions shortened to the same
    words would give a 3-element set matching a 3-element literal, and the four
    reasons would render identically on the glass again."""
    captions = {
        view.reset_note(series(source="spend", utilization=41), NOW)[0],
        view.reset_note(series(utilization=0), NOW)[0],
        view.reset_note(series(utilization=44), NOW)[0],
        view.reset_note(series(utilization=44,
                               resetsAt="2026-07-26T19:00:00Z"), NOW)[0],
    }
    assert len(captions) == 4, "four distinct sentences, not one and not three"
    assert captions == {"no reset", "window has not started",
                        "reset time unknown", "reset has passed"}


def test_reset_in_the_future_has_countdown_and_time():
    lead, at = view.reset_note(series(utilization=31,
                                      resetsAt="2026-07-26T20:00:00Z"), NOW)
    assert lead.startswith("reset in ") and at is not None
    assert "past reset" not in lead, "the rounding threshold must be the same as in countdown()"


def test_week_gets_day_of_week():
    """A reset 6 days out: the bare time lies, because it does not say which day.
    `at_stamp` decides that, the same one as in the web — the preposition is
    inside the stamp."""
    _, at = view.reset_note(series(resetsAt="2026-08-01T16:00:00Z", utilization=30),
                            NOW)
    preposition, day = at.split()[:2]
    assert preposition == "on" and day in fmt.DAYS


def test_no_series_does_not_blow_up():
    assert view.reset_note(None, NOW) == ("no data", None)


# --- series pickers ---------------------------------------------------------

def test_hero_stands_on_session_not_on_isActive():
    """Were the hero to follow isActive, the same screen would mean something else
    depending on the day of the week (HeroSession.tsx:20-22)."""
    week = series(seriesKey="limit:weekly_all|weekly|-|-", kind="weekly_all",
                     bucketKey="seven_day", isActive=True, utilization=99)
    session = series(isActive=False, utilization=3)
    assert view.pick_session([week, session]) is session


def test_pickers_skip_duplicates():
    """We render only primary series — bucket and limit are the same number
    reported twice (status.py:96-120)."""
    duplicate = series(seriesKey="bucket:five_hour", primary=False, kind=None,
                      utilization=2)
    real = series(utilization=2)
    assert view.pick_session([duplicate, real]) is real

    wk_dup = series(seriesKey="bucket:seven_day", primary=False, kind=None,
                    bucketKey="seven_day")
    wk = series(seriesKey="limit:weekly_all|weekly|-|-", kind="weekly_all",
                bucketKey="seven_day")
    assert view.pick_weekly([wk_dup, wk]) is wk


def test_week_takes_aggregate_not_scoped():
    """A deliberate decision: the row has to mean the same thing on every day of
    the week."""
    scoped = series(seriesKey="limit:weekly_scoped|weekly|fable|-",
                    kind="weekly_scoped", bucketKey="seven_day_fable",
                    utilization=99)
    aggregate = series(seriesKey="limit:weekly_all|weekly|-|-", kind="weekly_all",
                       bucketKey="seven_day", utilization=30)
    assert view.pick_weekly([scoped, aggregate]) is aggregate


def test_no_matching_series_gives_none():
    assert view.pick_session([]) is None
    assert view.pick_weekly([]) is None


# --- credits ----------------------------------------------------------------

def test_credits_have_three_states():
    """"off" and "unknown" are TWO DIFFERENT things (cascade.py:11-13)."""
    on = view.credits(model.CascadeRung({"key": "credits", "state": "on",
                                         "usedMinor": 3820, "limitMinor": 9000,
                                         "currency": "USD", "exponent": 2,
                                         "isCurrent": True}))
    assert (on.state, on.used, on.limit, on.currency) == ("on", "38.20", "90.00", "USD")
    assert on.bar_pct == pytest.approx(42.44, abs=0.01) and on.is_current

    assert view.credits(model.CascadeRung({"key": "credits", "state": "off"})).state == "off"
    assert view.credits(model.CascadeRung({"key": "credits", "state": "unknown"})).state == "unknown"
    assert view.credits(None).state == "unknown", "no rung is no knowledge"


def test_withdrawn_credits_show_amounts_instead_of_disappearing():
    """When the organization cuts credits off, the rung is `off`, but the amounts from
    the LAST measurement stay — the only thing that makes sense on 480x320. A row that
    vanishes loses a number the panel showed before; the panel never writes the reason
    for the withdrawal at all."""
    c = view.credits(model.CascadeRung({"key": "credits", "state": "off",
                                        "reason": "org_level_disabled_until",
                                        "usedMinor": 30004, "limitMinor": 30000,
                                        "currency": "EUR", "exponent": 2}))
    assert (c.used, c.limit, c.currency) == ("300.04", "300.00", "EUR")
    assert c.bar_pct == 100.0, "an overage must not push the bar apart"
    assert c.state == "off", "the state stays `off` — the amounts do not change it"


def test_credits_off_without_amounts_still_have_nothing_to_show():
    c = view.credits(model.CascadeRung({"key": "credits", "state": "off"}))
    assert c.state == "off" and c.used is None


def test_account_without_credits_does_not_get_a_zero_row():
    """An account that never had credits has `usedMinor: 0` and NO limit at all —
    Anthropic reports a zeroed `spend.used` there. A "0.00 / —" row would take room
    and say nothing; the row was not there before and is not to be there now."""
    c = view.credits(model.CascadeRung({"key": "credits", "state": "off",
                                        "usedMinor": 0, "currency": "USD",
                                        "exponent": 2}))
    assert c.used is None, "half a pair of amounts is not amounts"


def test_credits_without_limit_do_not_divide_by_zero():
    c = view.credits(model.CascadeRung({"key": "credits", "state": "on",
                                        "usedMinor": 100, "limitMinor": 0}))
    assert c.bar_pct == 0.0


# --- plan -------------------------------------------------------------------

@pytest.mark.parametrize("tier,want", [
    ("default_claude_max_5x", "Max 5×"),
    ("default_claude_max_20x", "Max 20×"),
    ("default_claude_team_standard", "Team Standard"),
])
def test_plan_from_token(tier, want):
    assert view.plan_label(model.AccountStatus({"rateLimitTier": tier})) == want


def test_unknown_tier_shows_up_raw():
    """Rule 5: a new token has to work without a migration. An empty label would
    break presentation rule 1 — the plan MUST be visible, because 40% means
    something else on Max 20x than on a Team seat."""
    a = model.AccountStatus({"rateLimitTier": "raven"})
    assert view.plan_label(a) == "Raven"


def test_plan_has_fallback_sources():
    assert view.plan_label(model.AccountStatus({"subscriptionType": "pro"})) == "Pro"
    assert view.plan_label(model.AccountStatus({"orgType": "claude_team"})) == "Team"
    assert view.plan_label(model.AccountStatus({})) == ""
    assert view.plan_label(None) == ""
