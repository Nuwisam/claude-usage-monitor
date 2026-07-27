"""Kaskada limitow: 5 h -> tydzien -> kredyty -> twardy blok.

Przypadek Max jest zbudowany na REALNYM payloadzie (fixtures/usage_max.json), przypadek
Team na jego opisie z docs/POC-FINDINGS.md — bo konto Team ma wyczerpany limit tygodniowy
i do jego powrotu nie da sie zaobserwowac wlaczonych kredytow. Gdy limit wroci, fixture
Team trzeba wymienic na prawdziwa odpowiedz.
"""
import json
from pathlib import Path

from app.parsing import parse_usage
from app.services.cascade import (
    CREDITS, HARD_BLOCK, OFF, ON, SESSION, UNKNOWN, WEEKLY, SeriesFacts, build_cascade,
)

REAL = json.loads((Path(__file__).parent / "fixtures" / "usage_max.json").read_text("utf-8"))


def facts_from_payload(payload) -> list[SeriesFacts]:
    """Ta sama droga, ktora chodzi produkcja: parse_usage -> fakty o seriach."""
    return [SeriesFacts(series_key=o.series_key, source=o.source, kind=o.kind,
                        bucket_key=o.bucket_key, utilization=o.utilization,
                        is_active=o.is_active, extra=o.extra)
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
def team_payload(used_minor=3820, limit_minor=9000, spend_limit_reached=False):
    """Team: tygodniowy wyczerpany, praca leci z kredytow. Kwoty w groszach/centach."""
    p = json.loads(json.dumps(REAL))
    for lim in p["limits"]:
        if lim["kind"] == "weekly_all":
            lim["percent"], lim["is_active"] = 100, True
        if lim["kind"] == "session":
            lim["percent"], lim["is_active"] = 12, False
    p["seven_day"]["utilization"] = 100.0
    p["five_hour"]["utilization"] = 12.0
    p["spend"] = {
        "enabled": True, "percent": 42, "severity": "normal",
        "used": {"amount_minor": used_minor, "currency": "USD", "exponent": 2},
        "limit": {"amount_minor": limit_minor, "currency": "USD", "exponent": 2},
    }
    p["extra_usage"] = dict(p["extra_usage"], is_enabled=True, credits_ever_enabled=True,
                            spend_limit_reached=spend_limit_reached)
    return p


def test_team_tygodniowy_na_100_procent_zsuwa_biezacy_szczebel_na_kredyty():
    """Najwazniejszy przypadek: `is_active` wskazuje tygodniowy, ale on jest wyczerpany,
    wiec realnie praca idzie z kredytow. Pokazanie tygodniowego jako biezacego bylo by
    myleniem 'to mnie ogranicza' z 'tu sie skonczylo'."""
    rungs = build_cascade(facts_from_payload(team_payload()))
    c = by_key(rungs)

    assert c[WEEKLY].utilization == 100.0
    assert c[CREDITS].state == ON
    assert current(rungs) == CREDITS


def test_team_kwoty_kredytow_w_jednostkach_mniejszych_bez_splaszczania():
    c = by_key(build_cascade(facts_from_payload(team_payload())))
    k = c[CREDITS]
    assert (k.used_minor, k.limit_minor, k.currency, k.exponent) == (3820, 9000, "USD", 2)
    # twardy blok stoi na tym samym progu, ktory limituje kredyty
    assert c[HARD_BLOCK].limit_minor == 9000


def test_team_wyczerpane_kredyty_zsuwaja_na_twardy_blok():
    rungs = build_cascade(facts_from_payload(
        team_payload(used_minor=9000, spend_limit_reached=True)))
    assert current(rungs) == HARD_BLOCK


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
