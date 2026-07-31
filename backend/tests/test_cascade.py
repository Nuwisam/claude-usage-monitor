"""Kaskada limitow: 5 h -> tydzien -> kredyty -> twardy blok.

WSZYSTKIE przypadki stoja na REALNYCH payloadach: Max na `usage_max.json`, Team na trzech
zrzutach z jednego konta (`tests/team.py`) — kredyty dzialajace, wlasna pula wyczerpana,
miernik wycofany przez organizacje. Wczesniej Team byl tu wymyslony (USD, prog 9000),
bo w chwili pisania nie dalo sie zaobserwowac wlaczonych kredytow; teraz sie da, wiec
syntetyk znika razem z liczbami, ktorych nikt nigdy nie zmierzyl.
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
    """Ta sama droga, ktora chodzi produkcja: parse_usage -> fakty o seriach."""
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
    # spend.enabled = false => kredyty WYLACZONE, a nie "nie wiem"
    assert c[CREDITS].state == OFF
    # bez kredytow twardy blok stoi zaraz za tygodniowym — brak kwoty progu
    assert c[HARD_BLOCK].state == ON and c[HARD_BLOCK].limit_minor is None


def test_max_wiaze_tygodniowy_bo_ma_is_active():
    rungs = build_cascade(facts_from_payload(REAL))
    assert current(rungs) == WEEKLY


def test_szczeble_okien_wskazuja_swoja_serie():
    c = by_key(build_cascade(facts_from_payload(REAL)))
    # UI podswietla serie, z ktorej wzieta jest wartosc — musi dostac jej klucz
    assert c[SESSION].series_key.startswith("limit:session")
    assert c[WEEKLY].series_key.startswith("limit:weekly_all")


# --------------------------------------------------------------------------- Team
def test_team_tygodniowy_na_100_procent_zsuwa_biezacy_szczebel_na_kredyty():
    """Najwazniejszy przypadek: `is_active` wskazuje tygodniowy, ale on jest wyczerpany,
    wiec realnie praca idzie z kredytow. Pokazanie tygodniowego jako biezacego bylo by
    myleniem 'to mnie ogranicza' z 'tu sie skonczylo'."""
    rungs = build_cascade(facts_from_payload(usage(USAGE_ACTIVE)))
    c = by_key(rungs)

    assert c[WEEKLY].utilization == 100.0
    assert c[CREDITS].state == ON
    assert current(rungs) == CREDITS


def test_team_kwoty_kredytow_w_jednostkach_mniejszych_bez_splaszczania():
    c = by_key(build_cascade(facts_from_payload(usage(USAGE_ACTIVE))))
    k = c[CREDITS]
    assert (k.used_minor, k.limit_minor, k.currency, k.exponent) == (27795, 30000, "EUR", 2)
    # twardy blok stoi na tym samym progu, ktory limituje kredyty
    assert c[HARD_BLOCK].limit_minor == 30000


def test_wyczerpana_pula_zsuwa_na_twardy_blok_mimo_spend_limit_reached_false():
    """Wyczerpanie WLASNEJ puli nie zapala zadnej flagi: `spend_limit_reached` stalo na
    `false` przy used 300,04 / limit 300,00 EUR. Jedyne, co ten stan wykrywa, to porownanie
    kwot — i dlatego ta galaz `_exhausted` ma tu wlasny test, zeby nie zniknela przy
    porzadkach jako 'martwy kod'."""
    p = usage(USAGE_POOL_EXHAUSTED)
    assert p["extra_usage"]["spend_limit_reached"] is False

    rungs = build_cascade(facts_from_payload(p))
    c = by_key(rungs)
    assert c[CREDITS].state == ON, "brama otwarta — kredyty sa wlaczone, tylko puste"
    assert c[CREDITS].used_minor >= c[CREDITS].limit_minor
    assert current(rungs) == HARD_BLOCK


def test_zuzycie_ponad_limit_nie_wywraca_kaskady_ani_kwot():
    """300,04 EUR z 300,00 to nadwyzka, nie blad. Kwoty nie moga byc przyciete ani ujemne,
    a procent zostaje na 100 — Anthropic tez go tam scina."""
    c = by_key(build_cascade(facts_from_payload(usage(USAGE_POOL_EXHAUSTED))))
    k = c[CREDITS]
    assert (k.used_minor, k.limit_minor, k.currency, k.exponent) == (30004, 30000, "EUR", 2)
    assert k.utilization == 100.0
    for r in c.values():
        assert r.utilization is None or 0.0 <= r.utilization <= 100.0


def test_cap_money_jest_czytany_gdy_limit_nie_przyszedl():
    """`cap` w realnej odpowiedzi jest ZAGNIEZDZONY: {"credits": null, "money": {...}}.
    Plaski odczyt bral sam zewnetrzny slownik, wiec fallback nigdy nie zadzialal na
    produkcyjnych danych — mimo ze test na wymyslonym, plaskim `cap` by go przepuscil."""
    p = usage(USAGE_ACTIVE)
    del p["spend"]["limit"]
    assert p["spend"]["cap"]["money"]["amount_minor"] == 30000

    c = by_key(build_cascade(facts_from_payload(p)))
    assert c[CREDITS].limit_minor == 30000
    assert c[CREDITS].currency == "EUR"
    assert c[HARD_BLOCK].limit_minor == 30000


# ------------------------------------------------- wycofany miernik (sufit organizacji)
def test_kaskada_na_wycofanym_mierniku_nie_obiecuje_sciezki_wyjscia():
    """Kredyty wycofane przez organizacje: szczebel jest `off`, bez liczby i bez progu —
    a `reason` mowi, dlaczego. Bez niego ten stan bylby nieodrozninalny od konta, ktore
    kredytow nigdy nie mialo, i UI obiecywaloby 'wlacz kredyty', gdy wlaczyc sie nie da."""
    c = by_key(build_cascade(facts_from_payload(usage(USAGE_WITHDRAWN))))

    assert c[CREDITS].state == OFF
    assert c[CREDITS].utilization is None
    assert c[CREDITS].limit_minor is None
    assert c[CREDITS].reason == "org_level_disabled_until"
    # Prog istnieje, ale jest poza kontraktem — kwoty dla niego nie ma zadnej.
    assert c[HARD_BLOCK].state == ON and c[HARD_BLOCK].limit_minor is None
    assert c[HARD_BLOCK].reason == "org_level_disabled_until"


def test_wycofane_kredyty_sa_pomijane_takze_bez_flagi_spend_limit_reached():
    """`spend_limit_reached` bywa `true` przy wycofaniu, ale opieranie sie na nim jest
    zgadywaniem: przy wyczerpanej wlasnej puli stoi `false`. Zsuniecie na twardy blok ma
    wynikac z tego, ze szczebel jest WYLACZONY, a nie z flagi."""
    p = usage(USAGE_WITHDRAWN)
    p["extra_usage"]["spend_limit_reached"] = False
    for lim in p["limits"]:
        if lim["kind"] == "weekly_all":
            lim["percent"], lim["is_active"] = 100, True

    rungs = build_cascade(facts_from_payload(p))
    assert by_key(rungs)[CREDITS].state == OFF
    assert current(rungs) == HARD_BLOCK


def test_konto_bez_kredytow_nie_dostaje_powodu():
    """Max, ktory kredytow nigdy nie wlaczyl: ten sam ksztalt payloadu, ale `reason` jest
    `null` — i tylko po tym da sie te dwa stany odroznic."""
    c = by_key(build_cascade(facts_from_payload(REAL)))
    assert c[CREDITS].state == OFF and c[CREDITS].reason is None
    assert c[HARD_BLOCK].reason is None


# ----------------------------------------------------------- brak danych != wylaczone
def test_brak_informacji_o_kredytach_daje_unknown_a_nie_off():
    """"Kredyty wylaczone" to informacja, "nie wiem, czy masz kredyty" to jej brak.
    Zlanie ich pokazywaloby sciezke wyjscia z limitu, ktorej moze nie byc."""
    p = json.loads(json.dumps(REAL))
    del p["spend"], p["extra_usage"]
    c = by_key(build_cascade(facts_from_payload(p)))

    assert c[CREDITS].state == UNKNOWN
    assert c[HARD_BLOCK].state == UNKNOWN


def test_pusty_zbior_serii_nie_wywala_i_daje_cztery_szczeble_unknown():
    rungs = build_cascade([])
    assert [r.key for r in rungs] == [SESSION, WEEKLY, CREDITS, HARD_BLOCK]
    assert all(r.state == UNKNOWN for r in rungs)
    assert current(rungs) is None       # nie zgadujemy, na czym stoisz


def test_nieznany_szczebel_zatrzymuje_zsuwanie():
    """Tygodniowy wyczerpany, o kredytach nic nie wiemy — biezacym szczeblem jest
    wtedy 'nie wiem', a nie twardy blok wybrany przez optymizm."""
    p = json.loads(json.dumps(REAL))
    for lim in p["limits"]:
        if lim["kind"] == "weekly_all":
            lim["percent"], lim["is_active"] = 100, True
    del p["spend"], p["extra_usage"]
    rungs = build_cascade(facts_from_payload(p))

    assert current(rungs) == CREDITS
    assert by_key(rungs)[CREDITS].state == UNKNOWN


def test_bucket_zastepuje_brakujacy_wpis_z_limits():
    """Gdyby Anthropic przestal podawac limits[], kaskada nadal ma dzialac z bucketow."""
    p = json.loads(json.dumps(REAL))
    del p["limits"]
    c = by_key(build_cascade(facts_from_payload(p)))

    assert c[SESSION].series_key == "bucket:five_hour"
    assert c[SESSION].utilization == REAL["five_hour"]["utilization"]
    assert c[WEEKLY].series_key == "bucket:seven_day"


def test_powod_bez_flagi_enabled_to_nadal_wylaczone_a_nie_nieznane():
    """Gdy blok przyjdzie okrojony — powod jest, flagi `enabled` nie ma — sam powod musi
    wystarczyc. Bez tego kaskada mowilaby 'nie wiem, czy masz kredyty' w chwili, gdy
    Anthropic wprost napisal, dlaczego ich nie masz."""
    facts = [SeriesFacts(series_key="spend:org", source="spend", utilization=None,
                         extra={"disabled_reason": "org_level_disabled_until"},
                         unavailable_reason="org_level_disabled_until")]
    c = by_key(build_cascade(facts))

    assert c[CREDITS].state == OFF, "powod bez flagi nie moze dawac UNKNOWN"
    assert c[CREDITS].reason == "org_level_disabled_until"
    assert c[HARD_BLOCK].state == ON
