"""DTO. Na zewnatrz camelCase, wewnatrz snake_case."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)


class CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=_camel, populate_by_name=True,
                              from_attributes=True)


# --------------------------------------------------------------------------- ingest
class IngestResult(CamelModel):
    ok: bool
    samples_written: int
    backlog_accepted: int = 0
    server_now: datetime
    batch_id: int | None = None
    series_registered: list[str] = []


# --------------------------------------------------------------------------- odczyt
class SeriesStatus(CamelModel):
    series_id: int
    series_key: str
    label: str
    source: str
    sort_order: int
    utilization: float | None          # None przy freshness == "unknown" — to jest poprawne
    raw_utilization: float | None      # ostatnia ZMIERZONA wartosc, bez wnioskowania
    resets_at: datetime | None
    seconds_to_reset: int | None
    captured_at: datetime | None
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
    last_sample_at: datetime | None
    last_batch_at: datetime | None
    last_client_host: str | None
    series: list[SeriesStatus]


class StatusResponse(CamelModel):
    contract_version: int
    server_now: datetime
    accounts: list[AccountStatus]
    warnings: list[str] = []


class HistoryPoint(CamelModel):
    t: datetime
    min: float | None
    max: float | None
    avg: float | None
    last: float | None
    n: int


class HistoryGap(CamelModel):
    from_: datetime
    to: datetime
    kind: str          # client_silent | no_samples


class HistoryResponse(CamelModel):
    bucket: str
    points: list[HistoryPoint]
    resets: list[datetime]
    gaps: list[HistoryGap]
