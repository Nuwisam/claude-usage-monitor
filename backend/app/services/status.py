"""Skladanie /api/status — jedno tanie zapytanie po series_state, nie groupwise-max.

Tu spina sie najwazniejsza regula projektu: stan `unknown` NIE moze zostac wyrenderowany
jako 0%. Dlatego `utilization` jest tam None, a obok jedzie `raw_utilization` z ostatnia
ZMIERZONA wartoscia — zeby UI moglo pokazac "ostatnio widziane 42%, ale nie wiemy co teraz"
bez udawania, ze to pomiar biezacy.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.freshness import display_utilization, freshness
from app.models import Account, IngestBatch, LimitSample, SeriesState, UsageSeries
from app.parsing import Sample, window_start_index
from app.schemas import AccountStatus, SeriesStatus, StatusResponse
from app.services.cascade import SeriesFacts, build_cascade
from app.services.ingest import utcnow

# v2: czas na drucie z offsetem (bylo bez), kind/group/bucketKey w seriach, cascade[]
# przy koncie, gaps[] w dwoch rodzajach.
# v3: confirmedAt + valueSince w seriach — swiezosc liczy sie od POTWIERDZENIA, nie od
#     zapisu probki. Bez tego dedup sprawia, ze niezmienna wartosc udaje utracona lacznosc.
# Podbicie = aktualizacja docs/UI-HANDOUT.md.
# deltaFrom doszlo PO v3 i wersji NIE podbija — dodanie pola nie lamie zgodnosci.
CONTRACT_VERSION = 3


async def _last_batch_times(db: AsyncSession) -> dict[int, datetime]:
    rows = (await db.execute(
        select(IngestBatch.account_id, func.max(IngestBatch.received_at))
        .where(IngestBatch.account_id.is_not(None))
        .group_by(IngestBatch.account_id)
    )).all()
    return {aid: t for aid, t in rows}


async def last_batch_time(db: AsyncSession, account_id: int) -> datetime | None:
    """Single-account variant for the SSE publisher — hits `ix_batches_acct_time` instead
    of grouping the whole table.

    The CALLER computes this fact and passes it into `build_account_status`. If the card
    builder fetched it itself there would be two queries for the same thing and two places
    to get it wrong — and the entire value of this refactor is that exactly one function
    assembles a card."""
    return (await db.execute(
        select(func.max(IngestBatch.received_at))
        .where(IngestBatch.account_id == account_id)
    )).scalar_one_or_none()


async def _recent_samples(db: AsyncSession, account_id: int,
                          since: datetime) -> dict[int, list[Sample]]:
    """Przebieg serii konta z ostatniej godziny, jednym zapytaniem — baseline delty trzeba
    przyciac do biezacego okna, wiec pojedynczy wiersz i tak nie wystarcza."""
    rows = (await db.execute(
        select(LimitSample.series_id, LimitSample.captured_at,
               LimitSample.utilization, LimitSample.resets_at)
        .where(LimitSample.account_id == account_id,
               LimitSample.captured_at >= since,
               LimitSample.stale_read.is_(False))
        .order_by(LimitSample.series_id, LimitSample.captured_at)
    )).all()
    out: dict[int, list[Sample]] = {}
    for sid, captured_at, util, resets_at in rows:
        out.setdefault(sid, []).append(
            (captured_at, float(util) if util is not None else None, resets_at))
    return out


def _delta_1h(rows: Sequence[Sample], *, now: datetime, current: float | None,
              resets_at: datetime | None) -> tuple[float | None, datetime | None]:
    """Przyrost zuzycia i czas probki, od ktorej go liczymy.

    Baseline PRZYCIETY DO BIEZACEGO OKNA: bez tego zaraz po resecie punktem odniesienia byla
    probka z poprzedniego okna i UI pisalo "-46 pp w ciagu godziny" przez cala godzine.
    """
    if current is None:
        return None, None
    if resets_at is not None and now > resets_at:
        # Wszystkie probki naleza do POPRZEDNIEGO okna. Warunek bez tolerancji, dokladnie jak
        # w freshness() — inaczej jedna karta pokazalaby "~0%" i delte ze starego okna.
        return None, None
    i = window_start_index(rows, settings.reset_window_eps_sec, settings.monotonic_eps)
    in_window = [(t, u) for t, u, _ in rows[i:] if u is not None]
    if len(in_window) < 2:
        return None, None      # jedna probka to nie rozpietosc
    t0, u0 = in_window[0]
    return round(current - u0, 4), t0


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


async def build_account_status(
    db: AsyncSession, account: Account, *, now: datetime, last_batch_at: datetime | None,
) -> tuple[AccountStatus, list[str]]:
    """The card for ONE account — the only place where one is assembled.

    Two paths call this: `/api/status` (a loop over accounts) and the SSE publisher (a
    single account, after ingest). Splitting them into two implementations would produce
    two cards for the same account that would have to stay identical forever — and no test
    watches over that indefinitely.

    `now` and `last_batch_at` come from outside on purpose: `/api/status` computes them
    once for the whole response (one consistent timestamp across accounts, one grouped
    query), the publisher for a single account. If this function fetched them itself, both
    facts would have two sources.

    Also returns this account's warnings — `warnings` in the response is a list across
    accounts, but every entry in it originates at one specific account.
    """
    a = account
    rows = (await db.execute(
        select(SeriesState, UsageSeries)
        .join(UsageSeries, UsageSeries.id == SeriesState.series_id)
        .where(SeriesState.account_id == a.id)
        .order_by(UsageSeries.sort_order, UsageSeries.series_key)
    )).all()

    lb = last_batch_at
    recent = await _recent_samples(db, a.id, now - timedelta(hours=1))
    warnings: list[str] = []
    series: list[SeriesStatus] = []
    facts: list[SeriesFacts] = []
    for st, s in rows:
        raw_all = float(st.last_utilization) if st.last_utilization is not None else None
        # Fakty dla kaskady zbieramy PRZED filtrem widoku: `extra:usage` na koncie bez
        # kredytow ma utilization = null i za chwile wypadnie, a kaskada z niego czyta.
        facts.append(SeriesFacts(
            series_key=s.series_key, source=s.source, kind=s.kind,
            bucket_key=s.bucket_key, utilization=raw_all,
            is_active=st.last_is_active, extra=st.last_extra,
        ))

        # Serie, ktore nigdy nie mialy wartosci (np. seven_day_opus na koncie bez Opusa)
        # rejestrujemy, ale nie zasmiecamy nimi widoku.
        if not s.ever_non_null and st.last_utilization is None:
            continue

        # Fallback na last_captured_at dla wierszy sprzed migracji dodajacej
        # last_confirmed_at — dla nich oba znaczenia sa i tak tozsame.
        confirmed = st.last_confirmed_at or st.last_captured_at
        state = freshness(
            now=now,
            confirmed_at=confirmed,
            resets_at=st.last_resets_at,
            last_batch_at=lb,
            fresh_window_sec=settings.fresh_window_sec,
            client_silent_sec=settings.client_silent_sec,
        )
        raw_u = raw_all
        shown = display_utilization(state, raw_u)
        secs = (int((st.last_resets_at - now).total_seconds())
                if st.last_resets_at is not None else None)
        # `shown`, nie `raw_u`: delta towarzyszy liczbie widocznej na ekranie, a przy
        # `unknown` liczby nie ma.
        d_pct, d_from = _delta_1h(recent.get(s.id, ()), now=now, current=shown,
                                  resets_at=st.last_resets_at)

        series.append(SeriesStatus(
            series_id=s.id, series_key=s.series_key, label=s.display_label,
            source=s.source, sort_order=s.sort_order,
            kind=s.kind, group=s.group_key, bucket_key=s.bucket_key,
            utilization=shown, raw_utilization=raw_u,
            resets_at=st.last_resets_at, seconds_to_reset=secs,
            captured_at=st.last_captured_at, confirmed_at=confirmed,
            value_since=st.value_since, freshness=state,
            is_active=st.last_is_active, severity=st.last_severity,
            delta_pct_1h=d_pct, delta_from=d_from,
            extra=st.last_extra,
        ))

    _mark_duplicates(series)

    # Bylo tu ostrzezenie "czesc serii na koncie X jest w stanie unknown". Zniklo razem
    # z samym pojeciem `unknown` w UI: widok pokazuje teraz ostatni ZMIERZONY procent, a to,
    # ile jest wart, mowi etykieta wieku odczytu przy kazdej serii. Baner powtarzal wiec
    # nazwa stanu to, co obok stalo juz liczba minut. `warnings[]` zostaje w kontrakcie —
    # dzis nikt go nie zapelnia i to jest poprawny stan, nie brak implementacji.

    card = AccountStatus(
        uuid=a.account_uuid, label=a.label, email=a.email,
        display_name=a.display_name, color=a.color,
        org_type=a.org_type, seat_tier=a.seat_tier,
        rate_limit_tier=a.org_rate_limit_tier or a.user_rate_limit_tier,
        subscription_type=a.subscription_type, is_enabled=a.is_enabled,
        last_sample_at=a.last_sample_at, last_batch_at=lb,
        last_client_host=a.last_client_host,
        cascade=build_cascade(facts), series=series,
    )
    return card, warnings


async def build_status(db: AsyncSession) -> StatusResponse:
    now = utcnow()
    warnings: list[str] = []
    last_batch = await _last_batch_times(db)

    accounts = (await db.execute(
        select(Account).where(Account.archived_at.is_(None)).order_by(Account.id)
    )).scalars().all()

    out: list[AccountStatus] = []
    for a in accounts:
        card, warn = await build_account_status(
            db, a, now=now, last_batch_at=last_batch.get(a.id)
        )
        out.append(card)
        warnings.extend(warn)

    return StatusResponse(contract_version=CONTRACT_VERSION, server_now=now,
                          accounts=out, warnings=warnings)
