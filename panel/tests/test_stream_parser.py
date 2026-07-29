"""Parser SSE i konfiguracja.

Parser ma jeden nieoczywisty obowiazek: `data:` moze wystapic WIELOKROTNIE
w jednej ramce, bo backend rozbija tak wieloliniowy JSON (events.py:47-56).
Sklejanie jest wymagane, nie kosmetyczne.
"""
import json

import pytest

from panel import config as C, stream


def zdarzenia(*chunks):
    return list(stream.parse_events(iter(chunks)))


def test_prosta_ramka():
    assert zdarzenia(b'event: ping\ndata: {"serverNow":"x"}\n\n') == \
        [("ping", {"serverNow": "x"})]


def test_data_w_wielu_liniach_jest_sklejane():
    """events.py:47-56 rozbija wieloliniowy JSON na kilka pol `data:`. Gdyby
    parser brał tylko ostatnie, kazda ramka z nowa linia w tresci bylaby zla."""
    body = json.dumps({"tekst": "pierwsza\ndruga"})
    raw = b"event: account\n"
    for line in body.split("\n"):
        raw += b"data: " + line.encode() + b"\n"
    raw += b"\n"
    assert zdarzenia(raw) == [("account", {"tekst": "pierwsza\ndruga"})]


def test_ramka_podzielona_miedzy_odczytami():
    """Najczestszy przypadek na prawdziwej sieci: ramka przychodzi w kawalkach."""
    assert zdarzenia(b'event: pi', b'ng\ndata: {"a"', b': 1}\n\n') == \
        [("ping", {"a": 1})]


def test_kilka_ramek_w_jednym_kawalku():
    got = zdarzenia(b'event: ping\ndata: {}\n\nevent: bye\ndata: {"reason":"lifetime"}\n\n')
    assert [e for e, _ in got] == ["ping", "bye"]


def test_retry_i_komentarze_sa_pomijane():
    got = zdarzenia(b'retry: 3000\n\n: komentarz\nevent: ping\ndata: {}\n\n')
    assert got == [("ping", {})]


def test_konce_linii_crlf():
    assert zdarzenia(b'event: ping\r\ndata: {}\r\n\r\n') == [("ping", {})]


def test_zly_json_nie_wywala_parsera():
    """Zepsuta ramka nie moze zabic panelu — nastepna i tak niesie pelny stan."""
    got = zdarzenia(b'event: account\ndata: {to nie json\n\nevent: ping\ndata: {}\n\n')
    assert got == [("account", None), ("ping", {})]


def test_pusty_strumien():
    assert zdarzenia(b"", b"") == []


def test_spacja_po_dwukropku_jest_opcjonalna():
    """Tak mowi gramatyka SSE. Nasz backend spacje wysyla zawsze, ale replay.py
    dostaje nagranie, ktore nie musi z niego pochodzic — a parser wymagajacy
    spacji zwracalby wtedy zero ramek i ani jednego bledu."""
    assert zdarzenia(b'event:ping\ndata:{"a":1}\n\n') == [("ping", {"a": 1})]


def test_pole_data_bez_wartosci_nie_psuje_ramki():
    assert zdarzenia(b'event: ping\ndata:\n\n') == [("ping", None)]


# --- konfiguracja -----------------------------------------------------------

def cfg(**kw):
    d = {"stream_token": "t", "account_1": {"uuid": "a"}}
    d.update(kw)
    return C.Config(d)


def test_konta_sa_dwoma_polami_w_kolejnosci_pasow():
    """Ksztalt konfiguracji jest ksztaltem ekranu: trzeciego konta nie da sie
    dopisac przez nieuwage."""
    c = cfg(account_1={"uuid": "a", "name": "gorne"},
            account_2={"uuid": "b", "name": "dolne"})
    assert [a.name for a in c.accounts] == ["gorne", "dolne"]
    assert not c.validate()


def test_samo_pierwsze_konto_wystarczy():
    assert cfg(account_2=None).accounts[0].uuid == "a"
    assert not cfg(account_2=None).validate()


@pytest.mark.parametrize("zmiana,fragment", [
    ({"stream_token": None}, "stream_token"),
    ({"account_1": None, "account_2": None}, "zadnego konta"),
    ({"account_2": {"uuid": "a"}}, "powtarza uuid"),
    ({"account_2": {"name": "bez uuid"}}, "bez uuid"),
    ({"account_2": "nie obiekt"}, "musi byc obiektem"),
    ({"brightness": 9}, "brightness"),
    ({"device": "nie obiekt"}, "device musi byc obiektem"),
])
def test_walidacja_lapie_bledy(zmiana, fragment):
    problemy = " ".join(cfg(**zmiana).validate())
    assert fragment in problemy


@pytest.mark.parametrize("zmiana,fragment", [
    ({"brightness": "jasno"}, "brightness musi byc liczba"),
    ({"tick_sec": "szybko"}, "tick_sec musi byc liczba"),
    ({"width": None}, "width musi byc liczba"),
    ({"height": 0}, "height musi byc >= 1"),
    # json.load przyjmuje gole `Infinity`, a int(float("inf")) to OverflowError,
    # nie ValueError — wiec goly except (TypeError, ValueError) go nie lapal.
    ({"brightness": float("inf")}, "brightness musi byc liczba"),
    ({"tick_sec": float("inf")}, "tick_sec musi byc liczba skonczona"),
    ({"tick_sec": float("nan")}, "tick_sec musi byc liczba skonczona"),
])
def test_zle_liczby_daja_problem_a_nie_wyjatek(zmiana, fragment):
    """validate() obiecuje ZWROCIC liste problemow. Gole `int(self.brightness)`
    rzucalo ValueError, ktory pod pythonw konczyl sie tracebackiem w logu
    i restartem zadania co minute — zamiast jednym zdaniem o tym, co poprawic."""
    problemy = " ".join(cfg(**zmiana).validate())
    assert fragment in problemy


def test_liczba_w_cudzyslowie_jest_konwertowana():
    """Sprawdzanie bez zapisu bylo pozorne: `int("480")` sie udaje, wiec validate()
    milczalo, a Layout liczyl potem `"480" - 1` i pekal TypeError-em juz PO tym,
    jak konfiguracje ogloszono zdatna do uzycia."""
    c = cfg(width="480", tick_sec="2")
    assert not c.validate()
    assert c.width == 480 and c.tick_sec == 2.0


def test_brak_pliku_to_inny_blad_niz_zepsuty_json(tmp_path):
    """Dwa rozne komunikaty: 'jeszcze nie zainstalowane' i 'zepsute przy edycji'."""
    with pytest.raises(C.ConfigError, match="brak pliku"):
        C.load(str(tmp_path / "nie-ma.json"))
    zly = tmp_path / "zly.json"
    zly.write_text("{to nie json", encoding="utf-8")
    with pytest.raises(C.ConfigError, match="niepoprawnym JSON"):
        C.load(str(zly))


def test_domyslne_wartosci_sa_dostepne():
    c = cfg()
    assert c.width == 480 and c.height == 320 and c.tick_sec == 1.0
    assert c.log_file.endswith("panel.log")
