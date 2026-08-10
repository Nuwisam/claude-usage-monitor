"""The limit cascade: 5 h -> week -> credits -> hard block.

PURE FUNCTIONS — zero I/O. The input is facts about series, the output four rungs.

Why this is in the backend and not in the UI: it is domain knowledge, not a pixel layout.
Observed on the Team account: before the 5 h and the weekly limit everything works normally,
then the work runs on credits, and at the end there is a hard block on the spend limit. To
read that, one has to reach into the untyped `spend` and `extra_usage` blocks — and that is
exactly why it has tests here instead of being glued together in a React component.

One rule above all others: **"off" and "unknown" are two different things.** "Credits are
off" is information, "I do not know whether you have credits" is an absence of information.
Merging them into one would show a certain way out of the limit that may not be there.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas import CascadeRung

ON, OFF, UNKNOWN = "on", "off", "unknown"
SESSION, WEEKLY, CREDITS, HARD_BLOCK = "session", "weekly", "credits", "hard_block"


@dataclass
class SeriesFacts:
    """The slice of series state the cascade needs. Built from SeriesState + UsageSeries
    BEFORE the filtering done for the view — `extra:usage` on an account without credits has
    utilization = null and drops out of `series[]`, and the cascade reads from exactly that.
    """
    series_key: str
    source: str
    kind: str | None = None
    bucket_key: str | None = None
    utilization: float | None = None       # MEASURED, without inference
    is_active: bool | None = None
    extra: dict[str, Any] | None = None
    # The reason this series' meter does not work (see parsing.meter_withdrawn).
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
    """Only a real bool means anything. A missing field => None, that is 'I do not know'."""
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

    # --- credits ----------------------------------------------------------
    # The withdrawal reason OUTRANKS the flag: when the organization shuts the gate, the meter
    # is zeroed, and we hold the only signal that tells this apart from a plain "there never
    # were any credits". Without it both states would look identical.
    reason = ((spend.unavailable_reason if spend else None)
              or (eu.unavailable_reason if eu else None))

    # Two independent sources of the same truth: `spend.enabled` and `extra_usage.is_enabled`.
    # We take the first one that is a real bool; neither present => we do not know.
    enabled = _flag(spend.extra if spend else None, "enabled")
    if enabled is None:
        enabled = _flag(eu.extra if eu else None, "is_enabled")
    if reason:
        enabled = False

    used_minor, used_cur, used_exp = _money((spend.extra or {}).get("used") if spend else None)
    lim_minor, lim_cur, lim_exp = _money((spend.extra or {}).get("limit") if spend else None)
    if lim_minor is None:
        # `cap` is sometimes an alternative name for the upper bound, but in a REAL response
        # it is nested: {"credits": null, "money": {"amount_minor": ...}}. A flat read
        # therefore took the outer dictionary alone and always came out empty.
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

    # --- hard block -------------------------------------------------------
    # When credits work, the block stands at the spend limit. When not — right after the weekly.
    if enabled is None:
        hard = CascadeRung(key=HARD_BLOCK, state=UNKNOWN)
    elif enabled:
        hard = CascadeRung(key=HARD_BLOCK, state=ON, limit_minor=lim_minor,
                           currency=lim_cur or used_cur,
                           exponent=lim_exp if lim_exp is not None else used_exp)
    else:
        # With a withdrawn meter the threshold exists, but it is OUTSIDE the contract: the
        # organization ceiling has neither an amount, nor a percent, nor `resets_at` in the
        # response. `reason` is here the only content we can give about it.
        hard = CascadeRung(key=HARD_BLOCK, state=ON, reason=reason)

    rungs = [_window_rung(SESSION, session), _window_rung(WEEKLY, weekly), credits, hard]
    _mark_current(rungs, session, weekly, eu)
    return rungs


def _exhausted(r: CascadeRung, eu: SeriesFacts | None) -> bool:
    if r.key == HARD_BLOCK:
        return False                      # terminal rung, there is no descending from it
    if r.key == CREDITS:
        if _flag(eu.extra if eu else None, "spend_limit_reached") is True:
            return True
        if r.used_minor is not None and r.limit_minor is not None:
            return r.used_minor >= r.limit_minor
        return False
    return r.utilization is not None and r.utilization >= 100.0


def _mark_current(rungs: list[CascadeRung], session: SeriesFacts | None,
                  weekly: SeriesFacts | None, eu: SeriesFacts | None) -> None:
    """`is_active` from limits[] says which window binds. If it is exhausted, the work
    actually runs from the rung below — that is exactly the observed Team case: the weekly
    one is `is_active` and stands at 100%, while in reality it runs on credits.
    """
    start = 0
    if weekly is not None and weekly.is_active:
        start = 1
    elif session is not None and session.is_active:
        start = 0
    elif session is None or session.utilization is None:
        start = 1                          # with no 5 h window we start from the weekly

    i, descended = start, False
    while i < len(rungs):
        r = rungs[i]
        if r.state == OFF:                 # a rung that is off gets skipped
            i, descended = i + 1, True
            continue
        if r.state == UNKNOWN:
            break                          # we do not guess — the UI will write "unknown"
        if _exhausted(r, eu):
            i, descended = i + 1, True
            continue
        break
    if i >= len(rungs):
        return
    # We stop on an unknown rung only when we REALLY descended here from something known
    # ("the weekly one is exhausted, what next — I do not know"). With zero knowledge about
    # all the rungs we point at none, because that would be guessing.
    if rungs[i].state == UNKNOWN and not descended:
        return
    rungs[i].is_current = True
