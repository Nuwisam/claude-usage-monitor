"""The limit cascade: 5 h -> week -> credits -> hard block.

EVERY case stands on REAL payloads: Max on `usage_max.json`, Team on three dumps from one
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
    assert len(cur) <= 1, "dokladnie jeden szczebel moze wiazac"
    return cur[0] if cur else None


# --------------------------------------------------------------------------- Max
def test_max_kredyty_wylaczone_blok_zaraz_za_tygodniowym():
    c = by_key(build_cascade(facts_from_payload(REAL)))

    assert c[SESSION].state == ON and c[SESSION].utilization == 20.0
    assert c[WEEKLY].state == ON and c[WEEKLY].utilization == 30.0
    # spend.enabled = false => credits SWITCHED OFF, and not "unknown"
    assert c[CREDITS].state == OFF
    # without credits the hard block stands right behind the weekly one — no threshold amount
    assert c[HARD_BLOCK].state == ON and c[HARD_BLOCK].limit_minor is None


def test_max_wiaze_tygodniowy_bo_ma_is_active():
    rungs = build_cascade(facts_from_payload(REAL))
    assert current(rungs) == WEEKLY


def test_szczeble_okien_wskazuja_swoja_serie():
    c = by_key(build_cascade(facts_from_payload(REAL)))
    # the UI highlights the series the value was taken from — it has to get its key
    assert c[SESSION].series_key.startswith("limit:session")
    assert c[WEEKLY].series_key.startswith("limit:weekly_all")


# --------------------------------------------------------------------------- Team
def test_team_tygodniowy_na_100_procent_zsuwa_biezacy_szczebel_na_kredyty():
    """The most important case: `is_active` points at the weekly one, but that one is
    exhausted, so the work really runs off credits. Showing the weekly one as current would
    confuse 'this is what limits you' with 'this is where it ran out'."""
    rungs = build_cascade(facts_from_payload(usage(USAGE_ACTIVE)))
    c = by_key(rungs)

    assert c[WEEKLY].utilization == 100.0
    assert c[CREDITS].state == ON
    assert current(rungs) == CREDITS


def test_team_kwoty_kredytow_w_jednostkach_mniejszych_bez_splaszczania():
    c = by_key(build_cascade(facts_from_payload(usage(USAGE_ACTIVE))))
    k = c[CREDITS]
    assert (k.used_minor, k.limit_minor, k.currency, k.exponent) == (27795, 30000, "EUR", 2)
    # the hard block stands on the same threshold that limits credits
    assert c[HARD_BLOCK].limit_minor == 30000


def test_wyczerpana_pula_zsuwa_na_twardy_blok_mimo_spend_limit_reached_false():
    """Exhausting the OWN pool lights no flag at all: `spend_limit_reached` stood at
    `false` with used 300.04 / limit 300.00 EUR. The only thing that detects this state is
    comparing the amounts — which is why the `_exhausted` branch has its own test here, so
    that a cleanup does not make it disappear as 'dead code'."""
    p = usage(USAGE_POOL_EXHAUSTED)
    assert p["extra_usage"]["spend_limit_reached"] is False

    rungs = build_cascade(facts_from_payload(p))
    c = by_key(rungs)
    assert c[CREDITS].state == ON, "brama otwarta — kredyty sa wlaczone, tylko puste"
    assert c[CREDITS].used_minor >= c[CREDITS].limit_minor
    assert current(rungs) == HARD_BLOCK


def test_zuzycie_ponad_limit_nie_wywraca_kaskady_ani_kwot():
    """300.04 EUR out of 300.00 is overage, not an error. The amounts must not be clipped
    or negative, and the percentage stays at 100 — Anthropic clips it there too."""
    c = by_key(build_cascade(facts_from_payload(usage(USAGE_POOL_EXHAUSTED))))
    k = c[CREDITS]
    assert (k.used_minor, k.limit_minor, k.currency, k.exponent) == (30004, 30000, "EUR", 2)
    assert k.utilization == 100.0
    for r in c.values():
        assert r.utilization is None or 0.0 <= r.utilization <= 100.0


def test_cap_money_jest_czytany_gdy_limit_nie_przyszedl():
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
def test_kaskada_na_wycofanym_mierniku_nie_obiecuje_sciezki_wyjscia():
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


def test_wycofane_kredyty_sa_pomijane_takze_bez_flagi_spend_limit_reached():
    """`spend_limit_reached` is sometimes `true` on withdrawal, but leaning on it is
    guesswork: with the own pool exhausted it stands at `false`. Sliding down to the hard
    block has to follow from the rung being SWITCHED OFF, not from the flag."""
    p = usage(USAGE_WITHDRAWN)
    p["extra_usage"]["spend_limit_reached"] = False
    for lim in p["limits"]:
        if lim["kind"] == "weekly_all":
            lim["percent"], lim["is_active"] = 100, True

    rungs = build_cascade(facts_from_payload(p))
    assert by_key(rungs)[CREDITS].state == OFF
    assert current(rungs) == HARD_BLOCK


def test_konto_bez_kredytow_nie_dostaje_powodu():
    """A Max that never switched credits on: the same payload shape, but `reason` is
    `null` — and that is the only thing by which the two states can be told apart."""
    c = by_key(build_cascade(facts_from_payload(REAL)))
    assert c[CREDITS].state == OFF and c[CREDITS].reason is None
    assert c[HARD_BLOCK].reason is None


# ------------------------------------------------------------ no data != switched off
def test_brak_informacji_o_kredytach_daje_unknown_a_nie_off():
    """"Credits are off" is information, "we do not know whether you have credits" is the
    absence of it. Merging them would show a way out of the limit that may not exist."""
    p = json.loads(json.dumps(REAL))
    del p["spend"], p["extra_usage"]
    c = by_key(build_cascade(facts_from_payload(p)))

    assert c[CREDITS].state == UNKNOWN
    assert c[HARD_BLOCK].state == UNKNOWN


def test_pusty_zbior_serii_nie_wywala_i_daje_cztery_szczeble_unknown():
    rungs = build_cascade([])
    assert [r.key for r in rungs] == [SESSION, WEEKLY, CREDITS, HARD_BLOCK]
    assert all(r.state == UNKNOWN for r in rungs)
    assert current(rungs) is None       # we do not guess what you are standing on


def test_nieznany_szczebel_zatrzymuje_zsuwanie():
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


def test_bucket_zastepuje_brakujacy_wpis_z_limits():
    """Were Anthropic to stop supplying limits[], the cascade must still work off buckets."""
    p = json.loads(json.dumps(REAL))
    del p["limits"]
    c = by_key(build_cascade(facts_from_payload(p)))

    assert c[SESSION].series_key == "bucket:five_hour"
    assert c[SESSION].utilization == REAL["five_hour"]["utilization"]
    assert c[WEEKLY].series_key == "bucket:seven_day"


def test_powod_bez_flagi_enabled_to_nadal_wylaczone_a_nie_nieznane():
    """When the block arrives trimmed — the reason is there, the `enabled` flag is not —
    the reason alone has to be enough. Without that the cascade would say 'we do not know
    whether you have credits' at the moment Anthropic wrote plainly why you do not."""
    facts = [SeriesFacts(series_key="spend:org", source="spend", utilization=None,
                         extra={"disabled_reason": "org_level_disabled_until"},
                         unavailable_reason="org_level_disabled_until")]
    c = by_key(build_cascade(facts))

    assert c[CREDITS].state == OFF, "powod bez flagi nie moze dawac UNKNOWN"
    assert c[CREDITS].reason == "org_level_disabled_until"
    assert c[HARD_BLOCK].state == ON
