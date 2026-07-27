"""Granice resetow — wyznaczane z probek, nie z koszyka.

Regresja, ktora to pokrywa: `resets[]` bylo wypelniane wylacznie w gale zi `bucket="raw"`,
a `bucket=auto` na 24 h wybiera `5m`. Efekt: domyslny widok historii nie mial ani jednej
granicy resetu — czyli brakowalo najwazniejszej linii na wykresie okna, ktore sie resetuje.
"""
from datetime import datetime, timedelta

from app.routers.read import _auto_bucket, _reset_boundaries

T0 = datetime(2026, 7, 26, 12, 0, 0)
R1 = datetime(2026, 7, 26, 15, 0, 0)
R2 = datetime(2026, 7, 26, 20, 0, 0)
R3 = datetime(2026, 7, 27, 1, 0, 0)


def at(mins):
    return T0 + timedelta(minutes=mins)


def test_zmiana_resets_at_daje_granice_w_momencie_zaobserwowania():
    rows = [(at(0), R1), (at(5), R1), (at(10), R2), (at(15), R2), (at(20), R3)]
    assert _reset_boundaries(rows) == [at(10), at(20)]


def test_pierwsza_probka_nie_jest_resetem():
    """Pierwsze `resets_at` jest tylko punktem odniesienia — okno wtedy nie wstalo."""
    assert _reset_boundaries([(at(0), R1), (at(5), R1)]) == []


def test_probki_bez_resets_at_sa_pomijane_bez_gubienia_punktu_odniesienia():
    # spend:org nie ma resets_at; taka seria nie ma granic i nie moze ich udawac
    assert _reset_boundaries([(at(0), None), (at(5), None)]) == []
    # a mieszanka nie gubi poprzedniej wartosci: None w srodku nie jest zmiana okna
    assert _reset_boundaries([(at(0), R1), (at(5), None), (at(10), R1)]) == []


def test_pusta_historia_nie_ma_granic():
    assert _reset_boundaries([]) == []


def test_kolysanie_granicy_okna_nie_jest_resetem():
    """Granica podawana przez Anthropic kolysze sie w obrebie JEDNEGO okna. Realny pomiar:
    49 probek w 3 h dalo wartosci od `00:59:59.014384` do `01:00:00.982268` — niecale
    2 sekundy rozstrzalu, przechodzace przez granice minuty.

    Na porownaniu doslownym historia rysowala granice resetu przy KAZDEJ probce:
    61 "resetow" na dobe dla okna, ktore resetuje sie 5 razy."""
    baza = datetime(2026, 7, 27, 0, 59, 59, 14384)
    rows = [(at(i), baza + timedelta(milliseconds=i * 100)) for i in range(20)]
    assert _reset_boundaries(rows) == []

    # ...ale prawdziwe przeskoczenie okna (5 h) nadal daje dokladnie jedna granice
    rows.append((at(25), baza + timedelta(hours=5)))
    assert _reset_boundaries(rows) == [at(25)]


def test_powolne_kolysanie_nie_sumuje_sie_do_falszywego_resetu():
    """Punkt odniesienia przesuwa sie z kazda probka, wiec dryf 1 s na probke przez cala
    dobe nadal nie przekroczy progu — inaczej po kilku godzinach powstalaby fikcyjna
    granica z samego sumowania szumu."""
    baza = datetime(2026, 7, 27, 0, 59, 59)
    rows = [(at(i), baza + timedelta(seconds=i)) for i in range(400)]
    assert _reset_boundaries(rows) == []


def test_auto_wybiera_koszyk_agregowany_dla_domyslnych_24h():
    """Dowod, ze regresja byla realna: domyslny zakres NIE jest 'raw'."""
    assert _auto_bucket(24 * 3600) == "5m"
    assert _auto_bucket(6 * 3600) == "raw"
    assert _auto_bucket(30 * 24 * 3600) == "1h"
