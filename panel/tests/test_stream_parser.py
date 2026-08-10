"""The SSE parser and the configuration.

The parser has one non-obvious duty: `data:` may appear MANY TIMES within
a single frame, because that is how the backend splits multi-line JSON (events.py:47-56).
Joining it back together is required, not cosmetic.
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
    """events.py:47-56 splits multi-line JSON into several `data:` fields. Were the
    parser to take the last one only, every frame with a newline inside would be wrong."""
    body = json.dumps({"tekst": "pierwsza\ndruga"})
    raw = b"event: account\n"
    for line in body.split("\n"):
        raw += b"data: " + line.encode() + b"\n"
    raw += b"\n"
    assert zdarzenia(raw) == [("account", {"tekst": "pierwsza\ndruga"})]


def test_ramka_podzielona_miedzy_odczytami():
    """The commonest case on a real network: the frame arrives in chunks."""
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
    """A broken frame must not kill the panel — the next one carries the full state anyway."""
    got = zdarzenia(b'event: account\ndata: {to nie json\n\nevent: ping\ndata: {}\n\n')
    assert got == [("account", None), ("ping", {})]


def test_pusty_strumien():
    assert zdarzenia(b"", b"") == []


def test_spacja_po_dwukropku_jest_opcjonalna():
    """That is what the SSE grammar says. Our backend always sends the space, but replay.py
    is handed a recording that need not come from it — and a parser demanding the
    space would then return zero frames and not a single error."""
    assert zdarzenia(b'event:ping\ndata:{"a":1}\n\n') == [("ping", {"a": 1})]


def test_pole_data_bez_wartosci_nie_psuje_ramki():
    assert zdarzenia(b'event: ping\ndata:\n\n') == [("ping", None)]


# --- configuration ----------------------------------------------------------

def cfg(**kw):
    d = {"stream_token": "t", "account_1": {"uuid": "a"}}
    d.update(kw)
    return C.Config(d)


def test_konta_sa_dwoma_polami_w_kolejnosci_pasow():
    """The shape of the configuration is the shape of the screen: a third account
    cannot be added by inattention."""
    c = cfg(account_1={"uuid": "a", "name": "gorne"},
            account_2={"uuid": "b", "name": "dolne"})
    assert [a.name for a in c.accounts] == ["gorne", "dolne"]
    assert not c.validate()


def test_samo_pierwsze_konto_wystarczy():
    assert cfg(account_2=None).accounts[0].uuid == "a"
    assert not cfg(account_2=None).validate()


@pytest.mark.parametrize("zmiana,fragment", [
    ({"stream_token": None}, "stream_token"),
    ({"account_1": None, "account_2": None}, "no account specified"),
    ({"account_2": {"uuid": "a"}}, "repeats the uuid"),
    ({"account_2": {"name": "bez uuid"}}, "has no uuid"),
    ({"account_2": "nie obiekt"}, "must be an object"),
    ({"brightness": 9}, "brightness"),
    ({"device": "nie obiekt"}, "device must be an object"),
])
def test_walidacja_lapie_bledy(zmiana, fragment):
    problemy = " ".join(cfg(**zmiana).validate())
    assert fragment in problemy


@pytest.mark.parametrize("zmiana,fragment", [
    ({"brightness": "jasno"}, "brightness must be a number"),
    ({"tick_sec": "szybko"}, "tick_sec must be a number"),
    ({"width": None}, "width must be a number"),
    ({"height": 0}, "height must be >= 1"),
    # json.load accepts a bare `Infinity`, and int(float("inf")) is an OverflowError,
    # not a ValueError — so a plain except (TypeError, ValueError) did not catch it.
    ({"brightness": float("inf")}, "brightness must be a number"),
    ({"tick_sec": float("inf")}, "tick_sec must be a finite number"),
    ({"tick_sec": float("nan")}, "tick_sec must be a finite number"),
])
def test_zle_liczby_daja_problem_a_nie_wyjatek(zmiana, fragment):
    """validate() promises to RETURN a list of problems. A bare `int(self.brightness)`
    threw a ValueError, which under pythonw ended in a traceback in the log
    and the task restarting every minute — instead of one sentence about what to fix."""
    problemy = " ".join(cfg(**zmiana).validate())
    assert fragment in problemy


def test_liczba_w_cudzyslowie_jest_konwertowana():
    """Checking without writing back was only apparent: `int("480")` succeeds, so validate()
    stayed silent, and Layout then computed `"480" - 1` and broke with a TypeError already
    AFTER the configuration had been declared fit for use."""
    c = cfg(width="480", tick_sec="2")
    assert not c.validate()
    assert c.width == 480 and c.tick_sec == 2.0


def test_brak_pliku_to_inny_blad_niz_zepsuty_json(tmp_path):
    """Two different messages: 'not installed yet' and 'broken while being edited'."""
    with pytest.raises(C.ConfigError, match="missing configuration file"):
        C.load(str(tmp_path / "nie-ma.json"))
    zly = tmp_path / "zly.json"
    zly.write_text("{to nie json", encoding="utf-8")
    with pytest.raises(C.ConfigError, match="invalid JSON"):
        C.load(str(zly))


def test_domyslne_wartosci_sa_dostepne():
    c = cfg()
    assert c.width == 480 and c.height == 320 and c.tick_sec == 1.0
    assert c.log_file.endswith("panel.log")
