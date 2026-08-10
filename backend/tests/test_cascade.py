"""The limit cascade: 5 h -> week -> credits -> hard block.

EVERY case rests on REAL payloads: Max on `usage_max.json`, Team on three dumps from one
account (`tests/team.py`) — credits working, the own pool exhausted, the meter withdrawn
by the organization. Team used to be made up here (USD, a threshold of 9000), because at
the time of writing there was no way to observe credits switched on; now there is, so the
synthetic goes away together with numbers nobody has ever measured.
"""
import json
from pathlib import Path

from app.parsing import parse_usage
from app.services.cascade import (
    CREDITS, HARD_BLOCK, OFF, ON, SESSION, UNKNOWN, WEEKLY, SeriesFacts, build_cascade,
)
from tests.team import USAGE_ACTIVE, USAGE_POOL_EXHAUSTED, USAGE_WITHDRAWN, usage

REAL = json.loads((Path(__file__).parent / "fixtures" / "usage_max.json").read_text("utf-8"))


def facts_from_payload(payload) -> list[SeriesFacts]:
    """The same road the running system walks: parse_usage -> facts about series."""
    return [SeriesFacts(series_key=o.series_key, source=o.source, kind=o.kind,
                        bucket_key=o.bucket_key, utilization=o.utilization,
                        is_active=o.is_active, extra=o.extra,
                        unavailable_reason=o.unavailable_reason)
            for o in parse_usage(payload).observations]


def by_key(rungs):
    return {r.key: r for r in rungs}


def current(rungs):
    cur = [r.key for r in rungs if r.is_current]
    assert len(cur) <= 1, "exactly one rung can be current"
    return cur[0] if cur else None


# --------------------------------------------------------------------------- Max
def test_max_credits_off_hard_block_right_behind_weekly():
    c = by_key(build_cascade(facts_from_payload(REAL)))

    assert c[SESSION].state == ON and c[SESSION].utilization == 20.0
    assert c[WEEKLY].state == ON and c[WEEKLY].utilization == 30.0
    # spend.enabled = false => credits SWITCHED OFF, and not "unknown"
    assert c[CREDITS].state == OFF
    # without credits the hard block sits right behind the weekly one — no threshold amount
    assert c[HARD_BLOCK].state == ON and c[HARD_BLOCK].limit_minor is None


def test_max_current_is_weekly_because_it_has_is_active():
    rungs = build_cascade(facts_from_payload(REAL))
    assert current(rungs) == WEEKLY


def test_window_rungs_point_at_their_own_series():
    c = by_key(build_cascade(facts_from_payload(REAL)))
    # the UI highlights the series the value was taken from — it has to get its key
    assert c[SESSION].series_key.startswith("limit:session")
    assert c[WEEKLY].series_key.startswith("limit:weekly_all")


# --------------------------------------------------------------------------- Team
def test_team_weekly_at_100_percent_slides_current_rung_to_credits():
    """The most important case: `is_active` points at the weekly one, but that one is
    exhausted, so the work really runs off credits. Showing the weekly one as current would
    confuse 'this is what limits you' with 'this is where it ran out'."""
    rungs = build_cascade(facts_from_payload(usage(USAGE_ACTIVE)))
    c = by_key(rungs)

    assert c[WEEKLY].utilization == 100.0
    assert c[CREDITS].state == ON
    assert current(rungs) == CREDITS


def test_team_credit_amounts_in_minor_units_without_flattening():
    c = by_key(build_cascade(facts_from_payload(usage(USAGE_ACTIVE))))
    k = c[CREDITS]
    assert (k.used_minor, k.limit_minor, k.currency, k.exponent) == (27795, 30000, "EUR", 2)
    # the hard block is pinned to the same threshold that limits credits
    assert c[HARD_BLOCK].limit_minor == 30000


def test_exhausted_pool_slides_to_hard_block_despite_spend_limit_reached_false():
    """Exhausting the OWN pool lights no flag at all: `spend_limit_reached` stood at
    `false` with used 300.04 / limit 300.00 EUR. The only thing that detects this state is
    comparing the amounts — which is why the `_exhausted` branch has its own test here, so
    that a cleanup does not make it disappear as 'dead code'."""
    p = usage(USAGE_POOL_EXHAUSTED)
    assert p["extra_usage"]["spend_limit_reached"] is False

    rungs = build_cascade(facts_from_payload(p))
    c = by_key(rungs)
    assert c[CREDITS].state == ON, "gate open — credits are on, just empty"
    assert c[CREDITS].used_minor >= c[CREDITS].limit_minor
    assert current(rungs) == HARD_BLOCK


def test_usage_over_limit_does_not_upend_cascade_or_amounts():
    """300.04 EUR out of 300.00 is overage, not an error. The amounts must not be clipped
    or negative, and the percentage stays at 100 — Anthropic clips it there too."""
    c = by_key(build_cascade(facts_from_payload(usage(USAGE_POOL_EXHAUSTED))))
    k = c[CREDITS]
    assert (k.used_minor, k.limit_minor, k.currency, k.exponent) == (30004, 30000, "EUR", 2)
    assert k.utilization == 100.0
    for r in c.values():
        assert r.utilization is None or 0.0 <= r.utilization <= 100.0


def test_nested_cap_money_is_read_when_the_limit_key_is_missing():
    """`cap` in the real response is NESTED: {"credits": null, "money": {...}}.
    A flat read took the outer dictionary itself, so the fallback never fired on real
    data — even though a test on a made-up, flat `cap` would have let it through."""
    p = usage(USAGE_ACTIVE)
    del p["spend"]["limit"]
    assert p["spend"]["cap"]["money"]["amount_minor"] == 30000

    c = by_key(build_cascade(facts_from_payload(p)))
    assert c[CREDITS].limit_minor == 30000
    assert c[CREDITS].currency == "EUR"
    assert c[HARD_BLOCK].limit_minor == 30000


# ----------------------------------------- withdrawn meter (the organization's ceiling)
def test_cascade_on_withdrawn_meter_does_not_promise_a_way_out():
    """Credits withdrawn by the organization: the rung is `off`, with no number and no
    threshold — and `reason` says why. Without it this state would be indistinguishable from
    an account that never had credits, and the UI would promise 'switch credits on' when
    switching them on is impossible."""
    c = by_key(build_cascade(facts_from_payload(usage(USAGE_WITHDRAWN))))

    assert c[CREDITS].state == OFF
    assert c[CREDITS].utilization is None
    assert c[CREDITS].limit_minor is None
    assert c[CREDITS].reason == "org_level_disabled_until"
    # The threshold exists, but it is outside the contract — there is no amount for it.
    assert c[HARD_BLOCK].state == ON and c[HARD_BLOCK].limit_minor is None
    assert c[HARD_BLOCK].reason == "org_level_disabled_until"


def test_withdrawn_credits_are_skipped_even_without_spend_limit_reached_flag():
    """`spend_limit_reached` is sometimes `true` on withdrawal, but leaning on it is
    guesswork: with the own pool exhausted it reads `false`. Sliding down to the hard
    block has to follow from the rung being SWITCHED OFF, not from the flag."""
    p = usage(USAGE_WITHDRAWN)
    p["extra_usage"]["spend_limit_reached"] = False
    for lim in p["limits"]:
        if lim["kind"] == "weekly_all":
            lim["percent"], lim["is_active"] = 100, True

    rungs = build_cascade(facts_from_payload(p))
    assert by_key(rungs)[CREDITS].state == OFF
    assert current(rungs) == HARD_BLOCK


def test_account_without_credits_gets_no_reason():
    """A Max that never switched credits on: the same payload shape, but `reason` is
    `null` — and that is the only thing by which the two states can be told apart."""
    c = by_key(build_cascade(facts_from_payload(REAL)))
    assert c[CREDITS].state == OFF and c[CREDITS].reason is None
    assert c[HARD_BLOCK].reason is None


# ------------------------------------------------------------ no data != switched off
def test_no_credit_information_gives_unknown_not_off():
    """"Credits are off" is information, "we do not know whether you have credits" is the
    absence of it. Merging them would show a way out of the limit that may not exist."""
    p = json.loads(json.dumps(REAL))
    del p["spend"], p["extra_usage"]
    c = by_key(build_cascade(facts_from_payload(p)))

    assert c[CREDITS].state == UNKNOWN
    assert c[HARD_BLOCK].state == UNKNOWN


def test_empty_series_set_does_not_blow_up_and_gives_four_unknown_rungs():
    rungs = build_cascade([])
    assert [r.key for r in rungs] == [SESSION, WEEKLY, CREDITS, HARD_BLOCK]
    assert all(r.state == UNKNOWN for r in rungs)
    assert current(rungs) is None       # we do not guess what you are standing on


def test_unknown_rung_stops_the_slide():
    """The weekly one is exhausted, about credits we know nothing — the current rung is
    then 'unknown', and not a hard block picked out of optimism."""
    p = json.loads(json.dumps(REAL))
    for lim in p["limits"]:
        if lim["kind"] == "weekly_all":
            lim["percent"], lim["is_active"] = 100, True
    del p["spend"], p["extra_usage"]
    rungs = build_cascade(facts_from_payload(p))

    assert current(rungs) == CREDITS
    assert by_key(rungs)[CREDITS].state == UNKNOWN


def test_bucket_replaces_missing_entry_from_limits():
    """Were Anthropic to stop supplying limits[], the cascade must still work off buckets."""
    p = json.loads(json.dumps(REAL))
    del p["limits"]
    c = by_key(build_cascade(facts_from_payload(p)))

    assert c[SESSION].series_key == "bucket:five_hour"
    assert c[SESSION].utilization == REAL["five_hour"]["utilization"]
    assert c[WEEKLY].series_key == "bucket:seven_day"


def test_reason_without_enabled_flag_is_still_off_not_unknown():
    """When the block arrives trimmed — the reason is there, the `enabled` flag is not —
    the reason alone has to be enough. Without that the cascade would say 'we do not know
    whether you have credits' at the moment Anthropic wrote plainly why you do not."""
    facts = [SeriesFacts(series_key="spend:org", source="spend", utilization=None,
                         extra={"disabled_reason": "org_level_disabled_until"},
                         unavailable_reason="org_level_disabled_until")]
    c = by_key(build_cascade(facts))

    assert c[CREDITS].state == OFF, "a reason without the flag must not give UNKNOWN"
    assert c[CREDITS].reason == "org_level_disabled_until"
    assert c[HARD_BLOCK].state == ON
