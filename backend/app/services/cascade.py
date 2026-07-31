"""Kaskada limitow: 5 h -> tydzien -> kredyty -> twardy blok.

CZYSTE FUNKCJE — zero I/O. Wejsciem sa fakty o seriach, wyjsciem cztery szczeble.

Dlaczego to jest w backendzie, a nie w UI: to wiedza dziedzinowa, nie uklad pikseli.
Zaobserwowana na koncie Team: przed limitem 5 h i tygodniowym wszystko dziala normalnie,
potem praca idzie z kredytow, a na koncu jest twardy blok na limicie wydatkow. Zeby to
odczytac, trzeba siegnac do nietypowanych blokow `spend` i `extra_usage` — i wlasnie
dlatego ma to testy tutaj, a nie sklejanie w komponencie React.

Jedna zasada ponad wszystkimi: **"off" i "unknown" to dwie rozne rzeczy.** "Kredyty
wylaczone" jest informacja, "nie wiem, czy masz kredyty" jest brakiem informacji. Zlanie
ich w jedno pokazywaloby pewna sciezke wyjscia z limitu, ktorej moze nie byc.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas import CascadeRung

ON, OFF, UNKNOWN = "on", "off", "unknown"
SESSION, WEEKLY, CREDITS, HARD_BLOCK = "session", "weekly", "credits", "hard_block"


@dataclass
class SeriesFacts:
    """Wycinek stanu serii, ktory kaskada potrzebuje. Budowany z SeriesState + UsageSeries
    PRZED filtrowaniem na potrzeby widoku — `extra:usage` na koncie bez kredytow ma
    utilization = null i wypada z `series[]`, a kaskada wlasnie z niego czyta.
    """
    series_key: str
    source: str
    kind: str | None = None
    bucket_key: str | None = None
    utilization: float | None = None       # ZMIERZONA, bez wnioskowania
    is_active: bool | None = None
    extra: dict[str, Any] | None = None
    # Powod, dla ktorego miernik tej serii nie dziala (patrz parsing.meter_withdrawn).
    unavailable_reason: str | None = None


def _pick(facts: list[SeriesFacts], **crit) -> SeriesFacts | None:
    for f in facts:
        if all(getattr(f, k) == v for k, v in crit.items()):
            return f
    return None


def _money(v: Any) -> tuple[int | None, str | None, int | None]:
    """{"amount_minor": 3820, "currency": "USD", "exponent": 2} -> (3820, "USD", 2)."""
    if not isinstance(v, dict):
        return (None, None, None)
    minor = v.get("amount_minor")
    if isinstance(minor, bool) or not isinstance(minor, int):
        minor = None
    cur = v.get("currency") if isinstance(v.get("currency"), str) else None
    exp = v.get("exponent")
    if isinstance(exp, bool) or not isinstance(exp, int):
        exp = None
    return (minor, cur, exp)


def _flag(extra: dict[str, Any] | None, key: str) -> bool | None:
    """Tylko prawdziwy bool cokolwiek znaczy. Brak pola => None, czyli 'nie wiem'."""
    if not isinstance(extra, dict):
        return None
    v = extra.get(key)
    return v if isinstance(v, bool) else None


def _window_rung(key: str, f: SeriesFacts | None) -> CascadeRung:
    if f is None:
        return CascadeRung(key=key, state=UNKNOWN)
    return CascadeRung(key=key, state=ON if f.utilization is not None else UNKNOWN,
                       utilization=f.utilization, series_key=f.series_key)


def build_cascade(facts: list[SeriesFacts]) -> list[CascadeRung]:
    session = _pick(facts, kind="session") or _pick(facts, bucket_key="five_hour")
    weekly = _pick(facts, kind="weekly_all") or _pick(facts, bucket_key="seven_day")
    spend = _pick(facts, source="spend")
    eu = _pick(facts, source="extra_usage")

    # --- kredyty ----------------------------------------------------------
    # Powod wycofania ma PIERWSZENSTWO przed flaga: gdy organizacja zamyka brame, licznik
    # jest wyzerowany, a my mamy w rekach jedyny sygnal, ktory to od zwyklego "kredytow
    # nigdy nie bylo" odroznia. Bez tego oba stany wygladalyby identycznie.
    reason = ((spend.unavailable_reason if spend else None)
              or (eu.unavailable_reason if eu else None))

    # Dwa niezalezne zrodla tej samej prawdy: `spend.enabled` i `extra_usage.is_enabled`.
    # Bierzemy pierwsze, ktore jest prawdziwym boolem; brak obu => nie wiemy.
    enabled = _flag(spend.extra if spend else None, "enabled")
    if enabled is None:
        enabled = _flag(eu.extra if eu else None, "is_enabled")
    if reason:
        enabled = False

    used_minor, used_cur, used_exp = _money((spend.extra or {}).get("used") if spend else None)
    lim_minor, lim_cur, lim_exp = _money((spend.extra or {}).get("limit") if spend else None)
    if lim_minor is None:
        # `cap` bywa alternatywna nazwa gornej granicy, ale w REALNEJ odpowiedzi jest
        # zagniezdzony: {"credits": null, "money": {"amount_minor": ...}}. Plaski odczyt
        # bral wiec sam zewnetrzny slownik i zawsze wychodzil pusty.
        cap = (spend.extra or {}).get("cap") if spend else None
        if isinstance(cap, dict) and isinstance(cap.get("money"), dict):
            cap = cap["money"]
        lim_minor, lim_cur, lim_exp = _money(cap)

    credits = CascadeRung(
        key=CREDITS,
        state=UNKNOWN if enabled is None else (ON if enabled else OFF),
        reason=reason,
        utilization=spend.utilization if (spend and enabled) else None,
        series_key=spend.series_key if spend else (eu.series_key if eu else None),
        used_minor=used_minor, limit_minor=lim_minor,
        currency=used_cur or lim_cur, exponent=used_exp if used_exp is not None else lim_exp,
    )

    # --- twardy blok ------------------------------------------------------
    # Gdy kredyty dzialaja, blok stoi na limicie wydatkow. Gdy nie — zaraz za tygodniowym.
    if enabled is None:
        hard = CascadeRung(key=HARD_BLOCK, state=UNKNOWN)
    elif enabled:
        hard = CascadeRung(key=HARD_BLOCK, state=ON, limit_minor=lim_minor,
                           currency=lim_cur or used_cur,
                           exponent=lim_exp if lim_exp is not None else used_exp)
    else:
        # Przy wycofanym mierniku prog istnieje, ale jest POZA kontraktem: sufit organizacji
        # nie ma w odpowiedzi ani kwoty, ani procentu, ani `resets_at`. `reason` jest tu
        # jedyna trescia, jaka mozemy o nim podac.
        hard = CascadeRung(key=HARD_BLOCK, state=ON, reason=reason)

    rungs = [_window_rung(SESSION, session), _window_rung(WEEKLY, weekly), credits, hard]
    _mark_current(rungs, session, weekly, eu)
    return rungs


def _exhausted(r: CascadeRung, eu: SeriesFacts | None) -> bool:
    if r.key == HARD_BLOCK:
        return False                      # szczebel terminalny, nie ma z niego zejscia
    if r.key == CREDITS:
        if _flag(eu.extra if eu else None, "spend_limit_reached") is True:
            return True
        if r.used_minor is not None and r.limit_minor is not None:
            return r.used_minor >= r.limit_minor
        return False
    return r.utilization is not None and r.utilization >= 100.0


def _mark_current(rungs: list[CascadeRung], session: SeriesFacts | None,
                  weekly: SeriesFacts | None, eu: SeriesFacts | None) -> None:
    """`is_active` z limits[] mowi, ktore okno wiaze. Jesli jest wyczerpane, praca
    faktycznie idzie ze szczebla nizej — to wlasnie zaobserwowany przypadek Team:
    tygodniowy jest `is_active` i ma 100%, a realnie leci z kredytow.
    """
    start = 0
    if weekly is not None and weekly.is_active:
        start = 1
    elif session is not None and session.is_active:
        start = 0
    elif session is None or session.utilization is None:
        start = 1                          # bez okna 5 h zaczynamy od tygodniowego

    i, descended = start, False
    while i < len(rungs):
        r = rungs[i]
        if r.state == OFF:                 # wylaczony szczebel sie pomija
            i, descended = i + 1, True
            continue
        if r.state == UNKNOWN:
            break                          # nie zgadujemy — UI napisze "nie wiem"
        if _exhausted(r, eu):
            i, descended = i + 1, True
            continue
        break
    if i >= len(rungs):
        return
    # Na nieznanym szczeblu stajemy tylko wtedy, gdy REALNIE tu zeszlismy z czegos znanego
    # ("tygodniowy wyczerpany, co dalej — nie wiem"). Przy zerowej wiedzy o wszystkich
    # szczeblach nie wskazujemy zadnego, bo to bylo by zgadywanie.
    if rungs[i].state == UNKNOWN and not descended:
        return
    rungs[i].is_current = True
