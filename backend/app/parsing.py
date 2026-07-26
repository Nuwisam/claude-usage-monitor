"""Normalizacja odpowiedzi /api/oauth/usage do plaskiej listy obserwacji.

CZYSTE FUNKCJE — zero I/O, zero zaleznosci od bazy. Cala logika, ktora moze sie mylic,
jest tutaj i jest testowalna na realnych payloadach z docs/POC-FINDINGS.md.

Zasady wyniesione z kroku 0:
  * Odpowiedz ma 17 kluczy najwyzszego poziomu, z czego 5 bylo dla nas nowych. Nie wolno
    zakladac zamknietej listy — parser akceptuje dowolny nowy klucz i zglasza go jako drift.
  * `bucket.utilization` to float, `limits[].percent` to int, `spend.percent` to int.
    Wszystko sprowadzamy do 0..100.
  * `resets_at` w surowej odpowiedzi to ISO-8601 z mikrosekundami i offsetem. Statusline
    podawal epoch — parser przyjmuje oba, bo to kosztuje trzy linie.
  * Na koncie Team wiazacym limitem jest `spend` (miesieczny limit organizacji), a nie okna
    czasowe. Dlatego `spend` jest seria pierwszej kategorii, nie polem pobocznym.
  * Kwoty w `spend` sa w jednostkach mniejszych z wykladnikiem — nie splaszczamy ich do float.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# Klucze najwyzszego poziomu obslugiwane osobno, nie jako zwykly bucket.
_SPECIAL_KEYS = {"limits", "extra_usage", "spend", "member_dashboard_available"}

_SORT = {
    "bucket:five_hour": 10,
    "bucket:seven_day": 20,
    "spend:org": 30,
    "extra:usage": 40,
}

_LABELS = {
    "five_hour": "Sesja (5 h)",
    "seven_day": "Tydzien (wszystkie modele)",
    "seven_day_opus": "Tydzien - Opus",
    "seven_day_sonnet": "Tydzien - Sonnet",
    "seven_day_omelette": "Tydzien - Design",
    "seven_day_cowork": "Tydzien - Cowork",
    "seven_day_oauth_apps": "Tydzien - aplikacje OAuth",
    "seven_day_overage_included": "Tydzien - z nadwyzka",
}

_KIND_LABELS = {
    "session": "Sesja",
    "weekly_all": "Tydzien (wszystkie modele)",
    "weekly_scoped": "Tydzien",
}


@dataclass
class Observation:
    series_key: str
    source: str                       # bucket | limit | extra_usage | spend
    display_label: str
    utilization: float | None         # ZAWSZE 0..100
    resets_at: datetime | None = None
    bucket_key: str | None = None
    kind: str | None = None
    group_key: str | None = None
    model_display_name: str | None = None
    surface_display_name: str | None = None
    is_active: bool | None = None
    severity: str | None = None
    sort_order: int = 1000
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    observations: list[Observation]
    seen_keys: set[str]               # wszystkie klucze najwyzszego poziomu w odpowiedzi
    null_keys: set[str]               # klucze obecne, ale None (seria istnieje, brak wartosci)
    problems: list[str]               # nie-fatalne: cokolwiek, czego nie umielismy odczytac


# --------------------------------------------------------------------------- pomocnicze
def parse_pct(v: Any) -> float | None:
    """Toleruje int, float i string z sufiksem '%'. Referencyjna implementacja musiala
    obsluzyc wszystkie trzy, wiec zakladamy to samo."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.strip().rstrip("%").strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def parse_ts(v: Any) -> datetime | None:
    """ISO-8601 (surowa odpowiedz) albo epoch w sekundach (statusline). Zwraca naiwny UTC,
    bo kolumny w bazie sa naiwne i wszystko trzymamy w UTC."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        # >1e12 => milisekundy; endpoint tego nie robi, ale tanio sie zabezpieczyc
        secs = float(v) / 1000.0 if float(v) > 1e12 else float(v)
        return datetime.fromtimestamp(secs, tz=timezone.utc).replace(tzinfo=None)
    if isinstance(v, str):
        s = v.strip().replace("Z", "+00:00")
        # fromisoformat w 3.12 radzi sobie z mikrosekundami i offsetem, ale nie z >6 cyframi
        m = re.match(r"^(.*\.\d{1,6})\d*(.*)$", s)
        if m:
            s = m.group(1) + m.group(2)
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt
    return None


def _slug(s: Any) -> str:
    if s is None:
        return "-"
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "-"


def humanize(key: str) -> str:
    return _LABELS.get(key) or key.replace("_", " ").capitalize()


def limit_series_key(kind, group, model, surface) -> str:
    key = "limit:%s|%s|%s|%s" % (_slug(kind), _slug(group), _slug(model), _slug(surface))
    if len(key) > 255:                       # nigdy w praktyce; parser nie ma prawa rzucic
        import hashlib
        key = key[:240] + ":h" + hashlib.sha256(key.encode()).hexdigest()[:8]
    return key


# --------------------------------------------------------------------------- glowne
def parse_usage(payload: Any) -> ParseResult:
    """Zamienia odpowiedz /api/oauth/usage na liste obserwacji.

    Nigdy nie rzuca. Czego nie umie odczytac, zglasza w `problems`, a reszte przetwarza —
    lepiej zapisac 15 z 17 serii niz odrzucic caly pomiar.
    """
    obs: list[Observation] = []
    problems: list[str] = []
    seen: set[str] = set()
    nulls: set[str] = set()

    if not isinstance(payload, dict):
        return ParseResult([], seen, nulls, ["payload nie jest obiektem"])

    for key, val in payload.items():
        seen.add(key)
        if val is None:
            nulls.add(key)

    # --- 1. buckety najwyzszego poziomu -----------------------------------
    for key, val in payload.items():
        if key in _SPECIAL_KEYS:
            continue
        if val is None:
            continue
        if not isinstance(val, dict):
            # np. member_dashboard_available=bool trafia tu tylko gdy zmieni sie schemat
            problems.append("klucz %s ma nieoczekiwany typ %s" % (key, type(val).__name__))
            continue
        if "utilization" not in val:
            problems.append("bucket %s bez pola utilization" % key)
            continue
        skey = "bucket:%s" % key
        extra = {k: v for k, v in val.items() if k not in ("utilization", "resets_at")}
        obs.append(Observation(
            series_key=skey, source="bucket", bucket_key=key,
            display_label=humanize(key),
            utilization=parse_pct(val.get("utilization")),
            resets_at=parse_ts(val.get("resets_at")),
            sort_order=_SORT.get(skey, 100),
            extra=extra,
        ))

    # --- 2. limits[] ------------------------------------------------------
    limits = payload.get("limits")
    if limits is not None and not isinstance(limits, list):
        problems.append("limits nie jest lista")
        limits = None
    for i, lim in enumerate(limits or []):
        if not isinstance(lim, dict):
            problems.append("limits[%d] nie jest obiektem" % i)
            continue
        scope = lim.get("scope") or {}
        model = ((scope.get("model") or {}).get("display_name")
                 if isinstance(scope, dict) else None)
        surface = ((scope.get("surface") or {}).get("display_name")
                   if isinstance(scope, dict) else None)
        kind, group = lim.get("kind"), lim.get("group")
        label = _KIND_LABELS.get(kind or "", (kind or "limit").replace("_", " "))
        if model:
            label = "%s - %s" % (label, model)
        if surface:
            label = "%s / %s" % (label, surface)
        obs.append(Observation(
            series_key=limit_series_key(kind, group, model, surface),
            source="limit", kind=kind, group_key=group,
            model_display_name=model, surface_display_name=surface,
            display_label=label,
            utilization=parse_pct(lim.get("percent")),
            resets_at=parse_ts(lim.get("resets_at")),
            is_active=bool(lim.get("is_active")) if lim.get("is_active") is not None else None,
            severity=lim.get("severity"),
            sort_order=15 if kind == "session" else (25 if kind == "weekly_all" else 200),
            extra={k: v for k, v in lim.items()
                   if k not in ("percent", "resets_at", "is_active", "severity",
                                "kind", "group", "scope")},
        ))

    # --- 3. extra_usage ---------------------------------------------------
    eu = payload.get("extra_usage")
    if isinstance(eu, dict):
        obs.append(Observation(
            series_key="extra:usage", source="extra_usage",
            display_label="Kredyty dodatkowe",
            utilization=parse_pct(eu.get("utilization")),
            sort_order=_SORT["extra:usage"],
            extra={k: v for k, v in eu.items() if k != "utilization"},
        ))
    elif eu is not None:
        problems.append("extra_usage nie jest obiektem")

    # --- 4. spend — na Team to JEST wiazacy limit --------------------------
    sp = payload.get("spend")
    if isinstance(sp, dict):
        obs.append(Observation(
            series_key="spend:org", source="spend",
            display_label="Limit wydatkow organizacji",
            utilization=parse_pct(sp.get("percent")),
            severity=sp.get("severity"),
            sort_order=_SORT["spend:org"],
            # Kwoty zostaja w jednostkach mniejszych z wykladnikiem — bez splaszczania.
            extra={k: v for k, v in sp.items() if k not in ("percent", "severity")},
        ))
    elif sp is not None:
        problems.append("spend nie jest obiektem")

    return ParseResult(obs, seen, nulls, problems)
