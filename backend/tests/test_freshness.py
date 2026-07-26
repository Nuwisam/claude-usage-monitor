"""Testy tabelaryczne czterech stanow swiezosci.

Najwazniejszy test w projekcie to `test_dzialajacy_klient_bez_probek_daje_unknown` —
sprawdza, ze narzedzie NIE pokaze falszywego zera, gdy sonda sie zepsuje.
"""
from datetime import datetime, timedelta

import pytest

from app.freshness import (
    INFERRED_RESET, LIVE, STALE, UNKNOWN, display_utilization, freshness,
)

NOW = datetime(2026, 7, 26, 20, 0, 0)


def m(minutes):
    return NOW - timedelta(minutes=minutes)


def test_swieza_probka():
    assert freshness(NOW, m(1), NOW + timedelta(hours=2), m(1)) == LIVE


def test_starsza_probka_w_trwajacym_oknie_jest_wciaz_prawdziwa():
    """utilization nie rosnie samo z siebie, wiec wartosc sprzed godziny jest nadal
    poprawna, dopoki okno sie nie zresetowalo."""
    assert freshness(NOW, m(60), NOW + timedelta(hours=1), m(60)) == STALE


def test_po_resecie_bez_aktywnosci_wnioskujemy_zero():
    """Okno minelo, klient milczal przez caly ten czas => nikt nic nie spalil."""
    reset = NOW - timedelta(hours=2)
    assert freshness(NOW, m(240), reset, m(240)) == INFERRED_RESET


def test_dzialajacy_klient_bez_probek_daje_unknown():
    """NAJWAZNIEJSZY PRZYPADEK.

    Okno sie zresetowalo, ale klient ZYL po resecie i mimo to nie ma probki dla tej serii.
    To awaria sondy, nie brak aktywnosci. Gdybysmy wnioskowali 0%, uzytkownik odpalilby
    duze zadanie w przekonaniu, ze ma pelny limit — i trafil w sciane.
    """
    reset = NOW - timedelta(hours=2)
    ostatni_batch = NOW - timedelta(minutes=5)      # klient zyje
    assert freshness(NOW, m(240), reset, ostatni_batch) == UNKNOWN


def test_dlugie_milczenie_klienta_w_trwajacym_oknie_daje_unknown():
    assert freshness(NOW, m(600), NOW + timedelta(hours=1), m(600),
                     client_silent_sec=3600) == UNKNOWN


def test_brak_probki_daje_unknown():
    assert freshness(NOW, None, None, None) == UNKNOWN


def test_brak_resets_at_nie_wywala():
    assert freshness(NOW, m(2), None, m(2)) == LIVE
    assert freshness(NOW, m(60), None, m(60)) == STALE


def test_granica_okna_swiezosci():
    assert freshness(NOW, NOW - timedelta(seconds=300), None, NOW, fresh_window_sec=300) == LIVE
    assert freshness(NOW, NOW - timedelta(seconds=301), None, NOW, fresh_window_sec=300) == STALE


@pytest.mark.parametrize("state,last,expected", [
    (LIVE, 42.0, 42.0),
    (STALE, 42.0, 42.0),
    (INFERRED_RESET, 42.0, 0.0),      # wnioskowanie, do oznaczenia wizualnie
    (UNKNOWN, 42.0, None),            # jedyna poprawna odpowiedz to brak liczby
])
def test_display_utilization(state, last, expected):
    assert display_utilization(state, last) == expected


def test_unknown_nigdy_nie_zwraca_zera():
    """Regresja na najgrozniejszy blad: UNKNOWN nie moze udawac 0%."""
    assert display_utilization(UNKNOWN, 0.0) is None
    assert display_utilization(UNKNOWN, 99.9) is None
