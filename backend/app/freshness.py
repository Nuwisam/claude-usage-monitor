"""Cztery stany swiezosci — czysta funkcja, testowalna tabelarycznie.

NAJWAZNIEJSZA REGULA W CALYM PROJEKCIE:
    Narzedzie musi umiec powiedziec "nie wiem" zamiast zgadywac.

Najgorszy tryb awarii to pokazanie falszywego, PEWNIE wygladajacego zera — bo na tej
podstawie odpalisz duze zadanie i trafisz w sciane. Dlatego rozrozniamy "okno sie
zresetowalo i nie bylo aktywnosci" (mozemy wnioskowac 0%) od "klient raportuje, ale nie
ma danych dla tej serii" (awaria — nie wolno pokazac 0%).

Rozroznienie opiera sie na `last_batch_at`, czyli na tym, czy klient w ogole sie odzywal.
Bez tej informacji oba przypadki wygladaja identycznie.
"""
from __future__ import annotations

from datetime import datetime

LIVE = "live"                       # swieza probka
STALE = "stale"                     # starsza, ale wciaz prawdziwa (utilization nie rosnie samo)
INFERRED_RESET = "inferred_reset"   # okno sie zresetowalo, brak aktywnosci => ~0%
UNKNOWN = "unknown"                 # nie wiemy; NIGDY nie renderowac jako 0%


def freshness(
    now: datetime,
    captured_at: datetime | None,
    resets_at: datetime | None,
    last_batch_at: datetime | None,
    fresh_window_sec: int = 300,
    client_silent_sec: int = 21600,
) -> str:
    if captured_at is None:
        return UNKNOWN

    age = (now - captured_at).total_seconds()
    if age <= fresh_window_sec:
        return LIVE

    if resets_at is not None and now > resets_at:
        # Okno przeturlalo sie po naszej ostatniej probce.
        if last_batch_at is not None and last_batch_at > resets_at:
            # Klient ZYL po resecie, a mimo to nie ma probki dla tej serii.
            # To nie cisza — to awaria. Nie wolno wnioskowac zera.
            return UNKNOWN
        # Klient milczal przez caly reset => nie bylo aktywnosci => okno naprawde puste.
        return INFERRED_RESET

    # Okno nadal trwa. Wartosc pozostaje prawdziwa, bo utilization nie rosnie samo z siebie.
    # Jesli klient milczy podejrzanie dlugo, wolimy przyznac sie do niewiedzy.
    if last_batch_at is None or (now - last_batch_at).total_seconds() > client_silent_sec:
        return UNKNOWN
    return STALE


def display_utilization(state: str, last_utilization: float | None) -> float | None:
    """Co pokazac. Przy INFERRED_RESET zwracamy 0.0, ale wolajacy MUSI oznaczyc to
    wizualnie jako wnioskowanie, a nie pomiar. Przy UNKNOWN zwracamy None — i to jest
    jedyna poprawna odpowiedz."""
    if state == INFERRED_RESET:
        return 0.0
    if state == UNKNOWN:
        return None
    return last_utilization
