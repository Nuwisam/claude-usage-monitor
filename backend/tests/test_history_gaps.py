"""Dwa rodzaje dziur w historii — czysta logika `_find_gaps`, bez bazy.

`client_silent` znaczy "nie pracowales", `no_samples` znaczy "klient dzialal, ale dla tej
serii nie przyszla ani jedna probka" — czyli AWARIA. To ta sama roznica, ktora w /status
odrozniia `inferred_reset` od `unknown`, i na wykresie musi wygladac inaczej. Wykres, ktory
maluje oba przypadki tak samo, klamie dokladnie tam, gdzie to narzedzie klamac nie moze.
"""
from datetime import datetime, timedelta

from app.routers.read import _find_gaps

T0 = datetime(2026, 7, 26, 12, 0, 0)
END = datetime(2026, 7, 26, 20, 0, 0)
PROG = timedelta(minutes=15)


def m(*mins):
    return [T0 + timedelta(minutes=x) for x in mins]


def kinds(gaps):
    return [(g.kind, g.from_, g.to) for g in gaps]


def test_brak_batchy_to_cisza_klienta():
    # batche co 5 min do 12:20, potem nic; probki tak samo
    ts = m(0, 5, 10, 15, 20)
    gaps = _find_gaps(T0, END, batch_times=ts, sample_times=ts, threshold=PROG)
    assert kinds(gaps) == [("client_silent", T0 + timedelta(minutes=20), END)]


def test_batche_sa_ale_brak_probek_serii_to_awaria_nie_bezczynnosc():
    """Serce tego testu: klient zyje, seria milczy."""
    batches = m(*range(0, 481, 5))          # batche przez caly zakres, co 5 min
    samples = m(0, 5, 10)                   # probki serii koncza sie o 12:10
    gaps = _find_gaps(T0, END, batch_times=batches, sample_times=samples, threshold=PROG)

    assert [g.kind for g in gaps] == ["no_samples"]
    assert gaps[0].from_ == T0 + timedelta(minutes=10)
    assert gaps[0].to == END


def test_cisza_klienta_nie_jest_liczona_drugi_raz_jako_brak_probek():
    """Gdy klient milczy, probek tez nie ma — ale to jeden fakt, nie dwa. Bez odejmowania
    kazda cisza malowalaby sie podwojnie, oboma wzorami naraz."""
    ts = m(0, 5, 10)
    gaps = _find_gaps(T0, END, batch_times=ts, sample_times=ts, threshold=PROG)
    assert [g.kind for g in gaps] == ["client_silent"]


def test_oba_rodzaje_w_jednym_zakresie_w_kolejnosci_czasu():
    # 12:00-12:10 dane, 12:10-13:00 cisza klienta, potem batche bez probek do konca
    batches = m(0, 5, 10) + m(*range(60, 481, 5))
    samples = m(0, 5, 10)
    gaps = _find_gaps(T0, END, batch_times=batches, sample_times=samples, threshold=PROG)

    assert [g.kind for g in gaps] == ["client_silent", "no_samples"]
    assert gaps[0].from_ == T0 + timedelta(minutes=10)
    assert gaps[0].to == T0 + timedelta(minutes=60)
    assert gaps[1].from_ == T0 + timedelta(minutes=60)   # zaczyna sie tam, gdzie cisza konczy
    assert gaps[1].to == END


def test_drzazgi_krotsze_niz_prog_nie_sa_zglaszane():
    """Na styku dwoch okresow ciszy zostaje kilkuminutowy odcinek z batchem. To nie jest
    awaria serii, tylko normalny odstep — nie ma prawa dostac wzoru na wykresie."""
    batches = m(0, 40, 45, 90)          # dwie ciszy z krotkim oddechem 12:40-12:45
    samples = m(0)
    gaps = _find_gaps(T0, T0 + timedelta(minutes=90),
                      batch_times=batches, sample_times=samples, threshold=PROG)
    assert [g.kind for g in gaps] == ["client_silent", "client_silent"]


def test_pusty_zakres_to_jedna_cisza_na_calosc():
    gaps = _find_gaps(T0, END, batch_times=[], sample_times=[], threshold=PROG)
    assert kinds(gaps) == [("client_silent", T0, END)]


def test_gesta_praca_bez_przerw_nie_daje_zadnych_dziur():
    ts = m(*range(0, 481, 5))
    assert _find_gaps(T0, END, batch_times=ts, sample_times=ts, threshold=PROG) == []
