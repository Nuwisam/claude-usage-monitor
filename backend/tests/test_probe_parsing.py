"""Testy klienckiej sondy (client/usage-probe.py).

Sonda jest jedynym kodem projektu dzialajacym w sciezce pracy uzytkownika, a przy tym
NIGDY nie rzuca wyjatkiem — wiec kazdy jej blad jest z definicji cichy. Stad testy tutaj,
mimo ze to nie jest kod backendu: to jedyne miejsce, gdzie cokolwiek ja sprawdza.

Ladujemy plik po sciezce, bo nazwa z myslnikiem nie jest poprawnym identyfikatorem
modulu, a zmiana nazwy zerwalaby istniejace konfiguracje hookow na maszynach.
"""
import importlib.util
import time
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[2] / "client" / "usage-probe.py"


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("usage_probe", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------- parser stdout
REAL_OUTPUT = """You are currently using your subscription to power your Claude Code usage

Current session: 48% used · resets Jul 27, 12:30pm (UTC)
Current week (all models): 47% used · resets Aug 1, 6pm (UTC)
Current week (Fable): 0% used
Current week (Sonnet only): 12% used

What's contributing to your limits usage?
Approximate, based on local sessions on this machine — does not include other devices.

Last 24h · 1104 requests · 5 sessions
  100% of your usage came from subagent-heavy sessions
  89% of your usage came from sessions active for 8+ hours
  Top subagents: Explore 5%, Plan 1%
"""


def test_parsuje_realne_wyjscie(probe):
    r = probe.parse_usage_text(REAL_OUTPUT)
    assert r["session"] == 48
    assert r["weekly_all"] == 47
    assert r["scoped"] == {"Fable": 0, "Sonnet only": 12}


def test_ignoruje_sekcje_atrybucji(probe):
    """Linie '100% of your usage came from...' maja procent, ale nie maja dwukropka
    przed nim. Gdyby sie lapaly, 100% wyladowaloby jako odczyt limitu."""
    r = probe.parse_usage_text(REAL_OUTPUT)
    assert 100 not in (r["session"], r["weekly_all"])
    assert 100 not in r["scoped"].values()


@pytest.mark.parametrize("bad", ["", None, "zupelnie co innego", "Current session: brak"])
def test_smieci_nie_wywracaja_parsera(probe, bad):
    r = probe.parse_usage_text(bad)
    assert r == {"session": None, "weekly_all": None, "scoped": {}}


def test_odrzuca_absurdalny_procent(probe):
    """Blad #52326 w Claude Code potrafi wstawic epoch z resets_at w pole procentu.
    Obciecie do 100 zamienialoby awarie w wiarygodny falszywy alarm, wiec odrzucamy."""
    r = probe.parse_usage_text("Current session: 1785143542% used\n"
                               "Current week (all models): 47% used")
    assert r["session"] is None
    assert r["weekly_all"] == 47


# ---------------------------------------------------------------------------- merge
def _cache(five=40, weekly=41, resets="2099-01-01T00:00:00+00:00"):
    return {
        "five_hour": {"utilization": five, "resets_at": resets},
        "seven_day": {"utilization": weekly, "resets_at": resets},
        "extra_usage": {"is_enabled": False},
        "spend": {"percent": 0, "severity": "normal", "enabled": False},
        "limits": [
            {"kind": "session", "group": "session", "percent": five,
             "severity": "normal", "resets_at": resets, "is_active": True, "scope": None},
            {"kind": "weekly_all", "group": "weekly", "percent": weekly,
             "severity": "normal", "resets_at": resets, "is_active": False, "scope": None},
            {"kind": "weekly_scoped", "group": "weekly", "percent": 0,
             "severity": "normal", "resets_at": None, "is_active": False,
             "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}},
        ],
    }


def test_merge_nadpisuje_swiezymi_procentami(probe):
    usage, applied = probe.merge(_cache(), {"session": 48, "weekly_all": 47,
                                            "scoped": {"Fable": 3}})
    assert usage["five_hour"]["utilization"] == 48
    assert usage["seven_day"]["utilization"] == 47
    by_kind = {l["kind"]: l for l in usage["limits"]}
    assert by_kind["session"]["percent"] == 48
    assert by_kind["weekly_all"]["percent"] == 47
    assert by_kind["weekly_scoped"]["percent"] == 3
    assert "five_hour" in applied and "seven_day" in applied


def test_merge_zachowuje_pola_ktorych_stdout_nie_ma(probe):
    """To jest cala racja bytu dwuzrodlowosci: stdout daje procenty, ale spend/extra_usage/
    severity/is_active istnieja wylacznie w cache."""
    usage, _ = probe.merge(_cache(), {"session": 48, "weekly_all": 47, "scoped": {}})
    assert usage["spend"]["severity"] == "normal"
    assert usage["extra_usage"]["is_enabled"] is False
    assert usage["limits"][0]["is_active"] is True


def test_merge_bez_swiezych_zwraca_cache_bez_zmian(probe):
    src = _cache()
    usage, applied = probe.merge(src, None)
    assert usage == src and applied == []


def test_merge_nie_mutuje_zrodla(probe):
    src = _cache()
    probe.merge(src, {"session": 99, "weekly_all": 99, "scoped": {}})
    assert src["five_hour"]["utilization"] == 40


# ------------------------------------------------------------------------- sanitize
PAST = "2020-01-01T00:00:00+00:00"


def test_wygasle_okno_bez_swiezych_wypada(probe):
    """Procent z okna, ktore juz sie zresetowalo, jest po prostu nieprawda — realnie
    jest teraz blisko zera. Publikacja dawnych 95% bylaby grubym bledem."""
    usage, _ = probe.merge(_cache(five=95, resets=PAST), None)
    usage, events = probe.sanitize(usage, [], time.time())
    assert usage["five_hour"] is None
    assert any("okno-wygaslo" in e for e in events)
    assert all(l["kind"] != "session" for l in usage["limits"])


def test_wygasle_okno_ze_swiezym_zachowuje_procent_ale_gubi_czas_resetu(probe):
    """Swiezy procent jest prawdziwy; nieaktualny jest wylacznie resets_at z cache.
    Zerujemy czas zamiast wyrzucac dobry pomiar — nastepny zapis cache poda nowy."""
    usage, applied = probe.merge(_cache(five=95, resets=PAST),
                                 {"session": 2, "weekly_all": 41, "scoped": {}})
    usage, events = probe.sanitize(usage, applied, time.time())
    assert usage["five_hour"]["utilization"] == 2
    assert usage["five_hour"]["resets_at"] is None
    assert any("reset-w-toku" in e for e in events)


def test_przyszly_reset_nie_jest_ruszany(probe):
    usage, applied = probe.merge(_cache(), {"session": 48, "weekly_all": 47, "scoped": {}})
    usage, events = probe.sanitize(usage, applied, time.time())
    assert usage["five_hour"]["resets_at"] == "2099-01-01T00:00:00+00:00"
    assert events == []
    assert len(usage["limits"]) == 3


def test_absurdalna_wartosc_w_cache_wypada(probe):
    usage, _ = probe.sanitize(_cache(five=1785143542), [], time.time())
    assert usage["five_hour"] is None


def test_sanitize_znosi_brakujacy_resets_at(probe):
    usage, events = probe.sanitize(_cache(resets=None), [], time.time())
    assert usage["five_hour"]["utilization"] == 40
    assert events == []
