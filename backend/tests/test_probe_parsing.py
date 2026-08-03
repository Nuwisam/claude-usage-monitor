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
    usage, covered = probe.merge(_cache(), {"session": 48, "weekly_all": 47,
                                            "scoped": {"Fable": 3}})
    assert usage["five_hour"]["utilization"] == 48
    assert usage["seven_day"]["utilization"] == 47
    by_kind = {l["kind"]: l for l in usage["limits"]}
    assert by_kind["session"]["percent"] == 48
    assert by_kind["weekly_all"]["percent"] == 47
    assert by_kind["weekly_scoped"]["percent"] == 3
    assert set(covered) == {"bucket:five_hour", "bucket:seven_day", "limit:session:-",
                            "limit:weekly_all:-", "limit:weekly_scoped:Fable"}


def test_pokrycie_to_nie_zmiana_wartosci(probe):
    """Swiezy odczyt ROWNY wartosci z cache jest potwierdzeniem, nie brakiem zdarzenia.
    Wczesniej liczyly sie tylko zmiany, przez co seria o niezmienionym procencie i wygaslym
    oknie wypadala z pomiaru zamiast dostac `reset-w-toku` — tracilismy jedyny prawdziwy
    odczyt. Od tego zbioru zalezy tez datowanie po stronie backendu."""
    _, covered = probe.merge(_cache(five=40, weekly=41),
                             {"session": 40, "weekly_all": 41, "scoped": {}})
    assert "bucket:five_hour" in covered and "bucket:seven_day" in covered


def test_merge_nie_pada_na_dziwnym_scope(probe):
    """`scope` jako string zamiast slownika: `merge` biegnie przed `log_local` i przed
    spoolem, wiec wyjatek tutaj kasowalby caly przebieg bez sladu — przy kazdym cyklu,
    dopoki cache ma ten ksztalt."""
    c = _cache()
    c["limits"][2]["scope"] = "global"
    c["limits"][0]["scope"] = ["cos", "zupelnie", "innego"]
    usage, covered = probe.merge(c, {"session": 48, "weekly_all": 47, "scoped": {"Fable": 3}})
    assert usage["limits"][0]["percent"] == 48
    assert "limit:weekly_scoped:-" not in covered, "bez modelu nie ma dopasowania procentu"


def test_zrzut_starszy_od_cache_jest_odrzucany(probe):
    """Zrzutowi wolno miec 900 s, a cache odswieza w tym czasie zwykla praca — kolejnosc
    potrafi sie odwrocic. Gdy miedzy zrzutem a cache wypadnie reset okna, nalozenie zrzutu
    daje procent SPRZED resetu przy granicy PO resecie: 95% przeciwko oknu, w ktorym realnie
    jest ~1%. `sanitize` tego nie widzi, bo granica jest wazna."""
    assert probe.dump_outdated(1000.0, 1120.0) is True
    assert probe.dump_outdated(1120.0, 1000.0) is False
    assert probe.dump_outdated(1000.0, 1000.0) is False, "rowny wiek to nie inwersja"
    # Brak ktoregokolwiek znacznika to nie jest dowod odwrocenia.
    assert probe.dump_outdated(None, 1000.0) is False
    assert probe.dump_outdated(1000.0, 0) is False


def test_merge_zachowuje_pola_ktorych_stdout_nie_ma(probe):
    """To jest cala racja bytu dwuzrodlowosci: stdout daje procenty, ale spend/extra_usage/
    severity/is_active istnieja wylacznie w cache."""
    usage, _ = probe.merge(_cache(), {"session": 48, "weekly_all": 47, "scoped": {}})
    assert usage["spend"]["severity"] == "normal"
    assert usage["extra_usage"]["is_enabled"] is False
    assert usage["limits"][0]["is_active"] is True


def test_merge_bez_swiezych_zwraca_cache_bez_zmian(probe):
    src = _cache()
    usage, covered = probe.merge(src, None)
    assert usage == src and covered == []


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
    usage, covered = probe.merge(_cache(five=95, resets=PAST),
                                 {"session": 2, "weekly_all": 41, "scoped": {}})
    usage, events = probe.sanitize(usage, covered, time.time())
    assert usage["five_hour"]["utilization"] == 2
    assert usage["five_hour"]["resets_at"] is None
    assert any("reset-w-toku" in e for e in events)


def test_przyszly_reset_nie_jest_ruszany(probe):
    usage, covered = probe.merge(_cache(), {"session": 48, "weekly_all": 47, "scoped": {}})
    usage, events = probe.sanitize(usage, covered, time.time())
    assert usage["five_hour"]["resets_at"] == "2099-01-01T00:00:00+00:00"
    assert events == []
    assert len(usage["limits"]) == 3


def test_absurdalna_wartosc_w_cache_wypada(probe):
    usage, _ = probe.sanitize(_cache(five=1785143542), [], time.time())
    assert usage["five_hour"] is None


def test_swiezy_procent_rowny_cache_ratuje_serie_z_wygaslym_oknem(probe):
    """Regresja: `sanitize` pyta o POKRYCIE, nie o zmiane. Gdy zrzut potwierdzil te sama
    wartosc, a okno w cache juz wygaslo, seria ma dostac `reset-w-toku` — dawniej wypadala
    z pomiaru w calosci, czyli tracilismy jedyny prawdziwy odczyt."""
    usage, covered = probe.merge(_cache(five=40, resets=PAST),
                                 {"session": 40, "weekly_all": 41, "scoped": {}})
    usage, events = probe.sanitize(usage, covered, time.time())
    assert usage["five_hour"]["utilization"] == 40
    assert usage["five_hour"]["resets_at"] is None
    assert any("reset-w-toku" in e for e in events)


def test_sanitize_nie_pada_na_dziwnym_scope(probe):
    """`sanitize` tez wyprowadza model limitu, wiec ten sam ksztalt musi przezyc i tutaj."""
    c = _cache(resets=PAST)
    c["limits"][2]["scope"] = "global"
    usage, covered = probe.merge(c, {"session": 2, "weekly_all": 41, "scoped": {}})
    usage, events = probe.sanitize(usage, covered, time.time())
    assert usage["five_hour"]["utilization"] == 2
    assert events


def test_sanitize_znosi_brakujacy_resets_at(probe):
    usage, events = probe.sanitize(_cache(resets=None), [], time.time())
    assert usage["five_hour"]["utilization"] == 40
    assert events == []


# ------------------------------------------------ odczyt ~/.claude.json (rodzina B)
# `_extract_block` i `read_claude_json` nie mialy do tej pory ANI JEDNEGO testu, mimo ze
# to wokol nich zbudowano reczne wycinanie zamiast `json.load`. Fixture'y to pelne, realne
# pliki z konta Team w trzech stanach kredytow — z wyczyszczonymi tozsamosciami, ale
# z zachowanym duplikatem klucza roznjacym sie wielkoscia liter i realnym rozmiarem,
# bo `_extract_block` chodzi po tekscie `text.find`-em.
def test_powod_wylaczenia_kredytow_rozroznia_trzy_stany(probe):
    """Jedyne pole, ktore samo odroznia wyczerpana WLASNA pule od sufitu organizacji:
    w pasmie `spend.disabled_reason` jest przy wyczerpanej puli `null`."""
    from tests.team import CLAUDE_JSON_STATES

    for path, oczekiwany in CLAUDE_JSON_STATES:
        text = path.read_text("utf-8")
        assert probe._extract_scalar(text, "cachedExtraUsageDisabledReason") == oczekiwany, \
            path.name


def test_wyciag_skalara_nie_rzuca_na_uszkodzonym_pliku(probe):
    """Zasada 3: sonda nigdy nie rzuca. Kazde wejscie ma dac wartosc albo None."""
    import json

    from tests.team import CLAUDE_JSON_COMPANY_EXHAUSTED

    KLUCZ = "cachedExtraUsageDisabledReason"
    pelny = CLAUDE_JSON_COMPANY_EXHAUSTED.read_text("utf-8")
    urwane_w_wartosci = pelny[: pelny.find(KLUCZ) + len(KLUCZ) + 8]
    for tekst in ("", "{", '{"inny": 1}', pelny[: pelny.find(KLUCZ)], urwane_w_wartosci,
                  pelny[: len(pelny) // 2]):
        assert probe._safe(probe._extract_scalar, tekst, KLUCZ) is None

    # Ucieta KONCOWKA pliku wartosci juz nie psuje i to jest cala przewaga recznego wyciagu
    # nad `json.load`, ktory na urwanym pliku nie zwraca NICZEGO.
    assert probe._extract_scalar(pelny[:-3], KLUCZ) == "org_level_disabled_until"
    assert probe._safe(json.loads, pelny[:-3]) is None

    # Wartosci nie-stringowe tez maja przejsc bez wyjatku — kontrakt jest otwarty.
    for surowe, oczekiwane in (('{"k": null}', None), ('{"k": 7}', 7),
                               ('{"k": true}', True), ('{"k":   "x"}', "x"),
                               ('{"k": "a\\"b"}', 'a"b')):
        assert probe._extract_scalar(surowe, "k") == oczekiwane


def test_wyciag_bloku_daje_to_samo_co_pelne_parsowanie(probe):
    """Reczne wycinanie musi zwracac DOKLADNIE to, co dalby parser calego pliku — inaczej
    sonda wysyla cos innego, niz Claude Code zapisal. Przy okazji utrwala zachowanie na
    duplikacie klucza roznjacym sie wielkoscia liter, ktory te pliki realnie maja."""
    import json

    from tests.team import CLAUDE_JSON_STATES

    for path, _ in CLAUDE_JSON_STATES:
        text = path.read_text("utf-8")
        pelne = json.loads(text)
        for klucz in ("oauthAccount", "cachedUsageUtilization"):
            assert probe._extract_block(text, klucz) == pelne[klucz], "%s / %s" % (
                path.name, klucz)

        klucze = [k for k in pelne["projects"]]
        assert any(k.lower() == j.lower() and k != j for k in klucze for j in klucze), \
            "%s stracil duplikat klucza — fixture przestal testowac to, po co powstal" % path.name


def test_read_claude_json_zwraca_konto_pomiar_i_powod_dla_kazdego_stanu(probe, monkeypatch):
    """Cala droga odczytu razem, na trzech realnych stanach. Brak pliku to nie blad —
    na swiezej maszynie Claude Code jeszcze go nie zapisal."""
    from tests.team import CLAUDE_JSON_STATES

    for path, oczekiwany in CLAUDE_JSON_STATES:
        monkeypatch.setattr(probe, "_find", lambda name, in_claude_dir=False, p=path: str(p))
        acct, cached, cfg_dir, reason = probe.read_claude_json()

        assert acct["emailAddress"] == "usage-monitor@example.test"
        assert acct["organizationType"] == "claude_team"
        assert isinstance(cached["utilization"]["spend"], dict)
        # Zasada 7: ingest kluczuje po accountUuid i obie strony musza sie zgadzac.
        assert cached["accountUuid"] == acct["accountUuid"]
        assert reason == oczekiwany

    monkeypatch.setattr(probe, "_find", lambda name, in_claude_dir=False: None)
    assert probe.read_claude_json() == (None, None, None, None)
