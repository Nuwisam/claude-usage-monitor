"""DTO. Na zewnatrz camelCase, wewnatrz snake_case.

CZAS NA DRUCIE JEST ZAWSZE UTC Z OFFSETEM (`...Z`) — patrz `UtcDt`. Kolumny w bazie sa
naiwne i tak zostaje; strefe dopinamy wylacznie przy serializacji.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, PlainSerializer


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)


def _utc_iso(v: datetime) -> str:
    """Naiwny UTC -> ISO-8601 z 'Z'.

    Bez tego przegladarka MILCZACO interpretuje "2026-07-26T19:07:37" jako czas lokalny
    i countdown przesuwa sie o strefe — liczba wyglada dobrze i jest zla. Caly projekt
    broni sie przed falszywa pewnoscia, a znacznik bez strefy jest jej czysta postacia.
    """
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# Uzywaj TEGO typu w kazdym polu wyjsciowym z data. Zwykly `datetime` serializuje sie
# bez offsetu i to jest pulapka, nie preferencja stylistyczna.
UtcDt = Annotated[datetime, PlainSerializer(_utc_iso, return_type=str)]


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True,
                              from_attributes=True)


# --------------------------------------------------------------------------- ingest
class IngestResult(CamelModel):
    ok: bool
    samples_written: int
    backlog_accepted: int = 0
    server_now: UtcDt
    batch_id: int | None = None
    series_registered: list[str] = []


# --------------------------------------------------------------------------- odczyt
class SeriesStatus(CamelModel):
    series_id: int
    series_key: str
    label: str
    source: str                        # bucket | limit | extra_usage | spend
    sort_order: int
    # Semantyka serii. Bez tych trzech pol UI musialoby zgadywac, ktora seria jest oknem
    # 5-godzinnym — po sort_order albo po prefiksie klucza, czyli dokladnie tak, jak
    # zabrania zasada 5 z AGENTS.md.
    kind: str | None                   # session | weekly_all | weekly_scoped | ...
    group: str | None                  # session | weekly | ...
    bucket_key: str | None             # five_hour, seven_day, tangelo, ...
    utilization: float | None          # None przy freshness == "unknown" — to jest poprawne
    raw_utilization: float | None      # ostatnia ZMIERZONA wartosc, bez wnioskowania
    resets_at: UtcDt | None
    seconds_to_reset: int | None
    captured_at: UtcDt | None
    freshness: str                     # live | stale | inferred_reset | unknown
    is_active: bool | None
    severity: str | None
    delta_pct_1h: float | None
    # API raportuje te same limity dwukrotnie: raz jako bucket najwyzszego poziomu,
    # raz jako wpis w limits[]. Wykrywamy to z danych (identyczne utilization + resets_at),
    # bez hardkodowanego mapowania — bo wlasnie hardkodowanie nas juz raz ugryzlo.
    # UI ma domyslnie pokazywac tylko primary=true.
    primary: bool = True
    duplicate_of: str | None = None
    extra: dict[str, Any] | None


class CascadeRung(CamelModel):
    """Jeden szczebel kaskady limitow: 5 h -> tydzien -> kredyty -> twardy blok.

    Kwoty w JEDNOSTKACH MNIEJSZYCH z wykladnikiem, nigdy jako float — i bez formatowania.
    Backend nie zwraca "38,20 / 90,00 USD"; sklada to UI, bo to jest prezentacja.
    """
    key: str                           # session | weekly | credits | hard_block
    state: str                         # on | off | unknown  ("unknown" != "off")
    is_current: bool = False           # szczebel, ktory ogranicza CIEBIE w tej chwili
    utilization: float | None = None   # session, weekly
    series_key: str | None = None      # powrot do serii, ktora dala te wartosc
    used_minor: int | None = None
    limit_minor: int | None = None
    currency: str | None = None
    exponent: int | None = None


class AccountStatus(CamelModel):
    uuid: str
    label: str | None
    email: str | None
    display_name: str | None
    color: str | None
    org_type: str | None
    seat_tier: str | None
    rate_limit_tier: str | None
    subscription_type: str | None
    is_enabled: bool
    last_sample_at: UtcDt | None
    last_batch_at: UtcDt | None
    last_client_host: str | None
    cascade: list[CascadeRung] = []
    series: list[SeriesStatus]


class StatusResponse(CamelModel):
    contract_version: int
    server_now: UtcDt
    accounts: list[AccountStatus]
    warnings: list[str] = []


class HistoryPoint(CamelModel):
    t: UtcDt
    min: float | None
    max: float | None
    avg: float | None
    last: float | None
    n: int


class HistoryGap(CamelModel):
    from_: UtcDt
    to: UtcDt
    # client_silent — klient milczal (brak batchy).
    # no_samples    — klient raportowal, ale dla TEJ serii nie przyszla ani jedna probka.
    # To dwie rozne rzeczy i na wykresie musza wygladac inaczej: druga jest AWARIA,
    # dokladnie tak samo jak stan `unknown` w /status.
    kind: str


class HistoryResponse(CamelModel):
    bucket: str
    points: list[HistoryPoint]
    resets: list[UtcDt]
    gaps: list[HistoryGap]
