"""Skladanie /api/status — jedno tanie zapytanie po series_state, nie groupwise-max.

Tu spina sie najwazniejsza regula projektu: stan `unknown` NIE moze zostac wyrenderowany
jako 0%. Dlatego `utilization` jest tam None, a obok jedzie `raw_utilization` z ostatnia
ZMIERZONA wartoscia — zeby UI moglo pokazac "ostatnio widziane 42%, ale nie wiemy co teraz"
bez udawania, ze to pomiar biezacy.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.freshness import display_utilization, freshness
from app.models import Account, IngestBatch, LimitSample, SeriesState, UsageSeries
from app.schemas import AccountStatus, SeriesStatus, StatusResponse
from app.services.ingest import utcnow

CONTRACT_VERSION = 1


async def _last_batch_times(db: AsyncSession) -> dict[int, datetime]:
    rows = (await db.execute(
        select(IngestBatch.account_id, func.max(IngestBatch.received_at))
        .where(IngestBatch.account_id.is_not(None))
        .group_by(IngestBatch.account_id)
    )).all()
    return {aid: t for aid, t in rows}


async def _delta_1h(db: AsyncSession, account_id: int, series_id: int,
                    now: datetime, current: float | None) -> float | None:
    if current is None:
        return None
    since = now - timedelta(hours=1)
    row = (await db.execute(
        select(LimitSample.utilization)
        .where(LimitSample.account_id == account_id,
               LimitSample.series_id == series_id,
               LimitSample.captured_at >= since,
               LimitSample.stale_read.is_(False))
        .order_by(LimitSample.captured_at.asc()).limit(1)
    )).scalar_one_or_none()
    if row is None:
        return None
    return round(current - float(row), 4)


def _mark_duplicates(series: list[SeriesStatus]) -> None:
    """API podaje te same limity dwukrotnie: `five_hour` i `limits[kind=session]`,
    `seven_day` i `limits[kind=weekly_all]`, `seven_day_<model>` i `weekly_scoped`.

    Nie mapujemy ich na sztywno — hardkodowanie zestawu bucketow juz raz okazalo sie bledne
    (5 z 17 kluczy bylo nieznanych). Zamiast tego parujemy po DANYCH: identyczne
    (utilization, resets_at) oznacza ten sam limit. Wpis z `limits[]` wygrywa, bo niesie
    `is_active` i `severity`, ktorych bucket nie ma.

    Gdy wartosci sie rozjada — a repo referencyjne opisuje, ze nowsze odpowiedzi zeruja
    starsze pola per-model — pary po prostu nie powstana i obie serie beda widoczne.
    To wlasciwe zachowanie: wolimy pokazac rozjazd niz go ukryc.
    """
    limits = [s for s in series if s.source == "limit"]
    for s in series:
        if s.source != "bucket" or s.raw_utilization is None:
            continue
        for l in limits:
            if l.raw_utilization is None:
                continue
            if (abs(l.raw_utilization - s.raw_utilization) < 1e-9
                    and l.resets_at == s.resets_at):
                s.primary = False
                s.duplicate_of = l.series_key
                break


async def build_status(db: AsyncSession) -> StatusResponse:
    now = utcnow()
    warnings: list[str] = []
    last_batch = await _last_batch_times(db)

    accounts = (await db.execute(
        select(Account).where(Account.archived_at.is_(None)).order_by(Account.id)
    )).scalars().all()

    out: list[AccountStatus] = []
    for a in accounts:
        rows = (await db.execute(
            select(SeriesState, UsageSeries)
            .join(UsageSeries, UsageSeries.id == SeriesState.series_id)
            .where(SeriesState.account_id == a.id)
            .order_by(UsageSeries.sort_order, UsageSeries.series_key)
        )).all()

        lb = last_batch.get(a.id)
        series: list[SeriesStatus] = []
        for st, s in rows:
            # Serie, ktore nigdy nie mialy wartosci (np. seven_day_opus na koncie bez Opusa)
            # rejestrujemy, ale nie zasmiecamy nimi widoku.
            if not s.ever_non_null and st.last_utilization is None:
                continue

            state = freshness(
                now=now,
                captured_at=st.last_captured_at,
                resets_at=st.last_resets_at,
                last_batch_at=lb,
                fresh_window_sec=settings.fresh_window_sec,
                client_silent_sec=settings.client_silent_sec,
            )
            raw_u = float(st.last_utilization) if st.last_utilization is not None else None
            shown = display_utilization(state, raw_u)
            secs = (int((st.last_resets_at - now).total_seconds())
                    if st.last_resets_at is not None else None)

            series.append(SeriesStatus(
                series_id=s.id, series_key=s.series_key, label=s.display_label,
                source=s.source, sort_order=s.sort_order,
                utilization=shown, raw_utilization=raw_u,
                resets_at=st.last_resets_at, seconds_to_reset=secs,
                captured_at=st.last_captured_at, freshness=state,
                is_active=st.last_is_active, severity=st.last_severity,
                delta_pct_1h=await _delta_1h(db, a.id, s.id, now, raw_u),
                extra=st.last_extra,
            ))

        _mark_duplicates(series)

        if any(x.freshness == "unknown" for x in series):
            warnings.append("Konto %s: cz. serii w stanie 'unknown' — sprawdz klienta"
                            % (a.label or a.account_uuid[:8]))

        out.append(AccountStatus(
            uuid=a.account_uuid, label=a.label, email=a.email,
            display_name=a.display_name, color=a.color,
            org_type=a.org_type, seat_tier=a.seat_tier,
            rate_limit_tier=a.org_rate_limit_tier or a.user_rate_limit_tier,
            subscription_type=a.subscription_type, is_enabled=a.is_enabled,
            last_sample_at=a.last_sample_at, last_batch_at=lb,
            last_client_host=a.last_client_host, series=series,
        ))

    return StatusResponse(contract_version=CONTRACT_VERSION, server_now=now,
                          accounts=out, warnings=warnings)
