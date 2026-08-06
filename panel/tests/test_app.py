"""Petla klienta — to, co robi z EKRANEM, zanim cokolwiek wie.

Panel trzyma ostatnia klatke bez podlaczonego hosta, wiec obraz z poprzedniego
biegu jest stanem wyjsciowym kazdego startu, a nie pusta kartka. Ten plik pilnuje
jedynej reguly, ktora z tego wynika: dopoki nie mamy danych ani pewnego powodu,
zeby zamalowac ekran, nie dotykamy go wcale.
"""
from panel import app as app_mod, config as C


def cfg(**kw):
    d = {"stream_token": "t", "account_1": {"uuid": "a"}}
    d.update(kw)
    return C.Config(d)


def test_przed_pierwszymi_danymi_nie_dotykamy_ekranu():
    """Regresja: `splash_after_sec` bramkowalo tylko pelnoekranowa karte stanu,
    a pasy z napisem "brak danych z serwera" szly na panel juz w pierwszym ticku.
    Kazdy restart wycieral wiec obraz z poprzedniego biegu — dokladnie to, czemu
    ten prog mial zapobiegac."""
    a = app_mod.App(cfg())
    assert a.holding()
    assert a.tick() is None, "tick nie moze zbudowac ani wyslac klatki"


def test_pierwsze_dane_otwieraja_rysowanie():
    a = app_mod.App(cfg())
    a.first_data_at = 1.0
    assert not a.holding()


def test_po_uplywie_progu_malujemy_karte_stanu():
    """Gdy dane nie przychodza wcale, w koncu trzeba powiedziec to wprost —
    milczacy panel z godzinnymi liczbami klamie bardziej niz komunikat."""
    a = app_mod.App(cfg())
    a.started -= a.cfg.splash_after_sec + 1
    assert not a.holding()
    assert a.screen().message, "po progu ma byc karta stanu, nie puste pasy"


def test_nieznane_zdarzenie_jest_no_opem():
    """Na tym stoi cala zgodnosc wstecz strumienia: serwer moze dolozyc ramke, ktorej
    ten panel nie zna, i nie wolno jej ani ruszyc modelu, ani udac swiezych danych.
    Dopoki nie bylo tego testu, wlasnosc byla przypadkiem konstrukcji, nie umowa."""
    a = app_mod.App(cfg())
    a.on_event("cos-czego-nie-znamy", {"serverNow": "2026-08-05T21:07:00Z",
                                       "account": {"uuid": "a"}})
    assert a.accounts == {}
    assert a.link_state == "down"
    assert a.first_data_at is None
    assert a.clock.anchored, "kotwica zegara jedzie z KAZDEJ ramki i to jest celowe"


def test_niezgodny_kontrakt_nie_czeka_na_prog():
    """Jedyna rzecz, ktora wiemy od razu i ktora uniewaznia poprzedni obraz:
    skoro nie rozumiemy ramek, stare liczby na szkle sa juz tylko dekoracja."""
    a = app_mod.App(cfg())
    a.contract_mismatch = 4
    assert not a.holding()
    assert a.screen().message
