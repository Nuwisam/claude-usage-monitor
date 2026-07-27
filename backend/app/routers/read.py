"""Endpointy odczytu — wszystkie za SSO.

Backend sam jest brama SSO (nie ma frontendu z nginx auth_request), wiec brak sesji daje
401 {reason, redirect_url}, a nie 302. UI musi na to zareagowac przekierowaniem — to jest
czesc kontraktu, nie szczegol implementacji.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_session
from app.models import (
    Account, IngestBatch, IngestEvent, LimitSample, Machine, MachineAccount,
    RawPayload, UsageSeries,
)
from app.parsing import same_reset_window
from app.schemas import HistoryGap, HistoryPoint, HistoryResponse, StatusResponse
from app.services.ingest import utcnow
from app.services.status import build_status
from app.sso import CurrentUser, require_authorized_user

router = APIRouter()


@router.get("/me")
async def me(user: CurrentUser = Depends(require_authorized_user)) -> dict:
    return {"email": user.email, "verifiedAt": user.verified_at}


@router.get("/status", response_model=StatusResponse)
async def status(
    user: CurrentUser = Depends(require_authorized_user),
    db: AsyncSession = Depends(get_session),
) -> StatusResponse:
    return await build_status(db)


@router.get("/accounts")
async def accounts(
    user: CurrentUser = Depends(require_authorized_user),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (await db.execute(select(Account).order_by(Account.id))).scalars().all()
    return [{
        "uuid": a.account_uuid, "label": a.label, "email": a.email,
        "displayName": a.display_name, "color": a.color, "isEnabled": a.is_enabled,
        "orgType": a.org_type, "orgName": a.org_name, "seatTier": a.seat_tier,
        "orgRateLimitTier": a.org_rate_limit_tier,
        "userRateLimitTier": a.user_rate_limit_tier,
        "subscriptionType": a.subscription_type,
        "extraUsageEnabled": a.extra_usage_enabled,
        "firstSeenAt": a.first_seen_at, "lastSampleAt": a.last_sample_at,
        "lastClientHost": a.last_client_host, "archivedAt": a.archived_at,
    } for a in rows]


@router.get("/machines")
async def machines(
    user: CurrentUser = Depends(require_authorized_user),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (await db.execute(
        select(Machine, MachineAccount, Account)
        .outerjoin(MachineAccount, MachineAccount.machine_id == Machine.id)
        .outerjoin(Account, Account.id == MachineAccount.account_id)
        .order_by(Machine.id)
    )).all()
    by: dict[int, dict] = {}
    for m, ma, a in rows:
        e = by.setdefault(m.id, {
            "name": m.name, "host": m.host, "ccVersion": m.cc_version,
            "scriptVersion": m.script_version, "firstSeenAt": m.first_seen_at,
            "lastSeenAt": m.last_seen_at, "batches": m.batches, "accounts": [],
        })
        if a is not None:
            e["accounts"].append({
                "uuid": a.account_uuid, "label": a.label, "email": a.email,
                "firstSeenAt": ma.first_seen_at, "lastSeenAt": ma.last_seen_at,
                "samples": ma.samples,
            })
    return list(by.values())


@router.get("/series")
async def series(
    user: CurrentUser = Depends(require_authorized_user),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (await db.execute(
        select(UsageSeries).order_by(UsageSeries.sort_order, UsageSeries.series_key)
    )).scalars().all()
    return [{
        "id": s.id, "seriesKey": s.series_key, "source": s.source,
        "label": s.display_label, "bucketKey": s.bucket_key, "kind": s.kind,
        "group": s.group_key, "modelDisplayName": s.model_display_name,
        "surfaceDisplayName": s.surface_display_name, "sortOrder": s.sort_order,
        "firstSeenAt": s.first_seen_at, "lastSeenAt": s.last_seen_at,
        "everNonNull": s.ever_non_null, "isActive": s.is_active,
    } for s in rows]


_BUCKETS = {"raw": 0, "1m": 60, "5m": 300, "1h": 3600}


def _auto_bucket(seconds: float) -> str:
    if seconds <= 6 * 3600:
        return "raw"
    if seconds <= 48 * 3600:
        return "5m"
    return "1h"


Interval = tuple[datetime, datetime]


def _quiet_intervals(times: list[datetime], from_: datetime, to: datetime,
                     threshold: timedelta) -> list[Interval]:
    """Przerwy dluzsze niz `threshold` w ciagu znacznikow, wliczajac poczatek i koniec
    zakresu. Pusta lista znacznikow => cisza na calym zakresie."""
    out: list[Interval] = []
    prev = from_
    for t in times:
        if t - prev > threshold:
            out.append((prev, t))
        prev = t
    if to - prev > threshold:
        out.append((prev, to))
    return out


def _subtract(spans: list[Interval], holes: list[Interval],
              min_len: timedelta) -> list[Interval]:
    """spans minus holes. Odcinki krotsze niz `min_len` odrzucamy — inaczej na styku
    dwoch okresow ciszy zostawaly kilkusekundowe drzazgi udajace awarie."""
    out: list[Interval] = []
    for a, b in spans:
        cur = a
        for ha, hb in sorted(holes):
            if hb <= cur or ha >= b:
                continue
            if ha > cur and ha - cur >= min_len:
                out.append((cur, min(ha, b)))
            cur = max(cur, hb)
            if cur >= b:
                break
        if cur < b and b - cur >= min_len:
            out.append((cur, b))
    return out


def _reset_boundaries(rows: list[tuple[datetime, datetime | None]]) -> list[datetime]:
    """Momenty, w ktorych okno sie zresetowalo — czyli w ktorych zmienil sie `resets_at`.

    Liczone z probek, a NIE wewnatrz konkretnego koszyka: wczesniej siedzialo to w gale zi
    trybu `raw`, wiec kazdy zakres powyzej 6 h (w tym domyslne 24 h) wracal bez ani jednej
    granicy. Pierwsza zaobserwowana wartosc nie jest resetem — to tylko punkt odniesienia.

    Porownanie idzie przez `same_reset_window`, czyli Z TOLERANCJA — granica podawana przez
    Anthropic kolysze sie o okolo 2 s w obrebie jednego okna. Na rownosc historia rysowala
    granice resetu przy kazdej probce: zmierzone 61 "resetow" na dobe dla okna, ktore
    resetuje sie 5 razy.
    """
    out: list[datetime] = []
    prev: datetime | None = None
    for captured_at, resets_at in rows:
        if resets_at is None:
            continue
        if prev is None:
            prev = resets_at
            continue
        if not same_reset_window(prev, resets_at, settings.reset_window_eps_sec):
            out.append(captured_at)
        # Punkt odniesienia przesuwamy zawsze, zeby powolne kolysanie nie sumowalo sie
        # do przekroczenia progu po kilku godzinach.
        prev = resets_at
    return out


def _find_gaps(from_: datetime, to: datetime, batch_times: list[datetime],
               sample_times: list[datetime], threshold: timedelta) -> list[HistoryGap]:
    """Dwa rodzaje dziur, bo znacza dwie rozne rzeczy.

    `client_silent` — nie bylo batchy. Nie pracowales, wiec nie ma czego mierzyc.
    `no_samples`    — batche przychodzily, ale dla TEJ serii nie bylo ani jednej probki.

    Drugi przypadek to AWARIA, dokladnie ta sama, ktora w /status daje `unknown`. Wykres,
    ktory maluje oba tak samo, klamie w jedynym miejscu, w ktorym to narzedzie klamac nie
    moze — bo bezczynnosc wyglada wtedy identycznie jak zepsuty klient.
    """
    silent = _quiet_intervals(batch_times, from_, to, threshold)
    no_samples = _subtract(
        _quiet_intervals(sample_times, from_, to, threshold), silent, threshold
    )
    gaps = ([HistoryGap(from_=a, to=b, kind="client_silent") for a, b in silent]
            + [HistoryGap(from_=a, to=b, kind="no_samples") for a, b in no_samples])
    gaps.sort(key=lambda g: g.from_)
    return gaps


@router.get("/history", response_model=HistoryResponse)
async def history(
    account: str = Query(..., description="account_uuid"),
    series_id: int = Query(..., alias="seriesId"),
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    bucket: str = Query("auto"),
    user: CurrentUser = Depends(require_authorized_user),
    db: AsyncSession = Depends(get_session),
) -> HistoryResponse:
    now = utcnow()
    to = to or now
    from_ = from_ or (to - timedelta(hours=24))

    acc = (await db.execute(
        select(Account).where(Account.account_uuid == account)
    )).scalar_one_or_none()
    if acc is None:
        raise HTTPException(status_code=404, detail={"reason": "account-not-found"})

    if bucket == "auto":
        bucket = _auto_bucket((to - from_).total_seconds())
    if bucket not in _BUCKETS:
        raise HTTPException(status_code=400, detail={"reason": "bad-bucket"})
    width = _BUCKETS[bucket]

    base = (select(LimitSample)
            .where(LimitSample.account_id == acc.id,
                   LimitSample.series_id == series_id,
                   LimitSample.captured_at >= from_,
                   LimitSample.captured_at <= to,
                   LimitSample.stale_read.is_(False))
            .order_by(LimitSample.captured_at))

    points: list[HistoryPoint] = []
    if width == 0:
        rows = (await db.execute(base)).scalars().all()
        for r in rows:
            u = float(r.utilization) if r.utilization is not None else None
            points.append(HistoryPoint(t=r.captured_at, min=u, max=u, avg=u, last=u, n=1))
    else:
        # Downsampling z min/max, zeby piki przezyly agregacje — bez tego wykres klamie.
        slot = func.floor(func.unix_timestamp(LimitSample.captured_at) / width) * width
        rows = (await db.execute(
            select(slot.label("slot"),
                   func.min(LimitSample.utilization), func.max(LimitSample.utilization),
                   func.avg(LimitSample.utilization),
                   func.substring_index(
                       func.group_concat(LimitSample.utilization.op("ORDER BY")(
                           LimitSample.captured_at)), ",", -1),
                   func.count())
            .where(LimitSample.account_id == acc.id,
                   LimitSample.series_id == series_id,
                   LimitSample.captured_at >= from_,
                   LimitSample.captured_at <= to,
                   LimitSample.stale_read.is_(False))
            .group_by("slot").order_by("slot")
        )).all()
        for s, mn, mx, avg, last, n in rows:
            points.append(HistoryPoint(
                t=datetime.utcfromtimestamp(float(s)),
                min=float(mn) if mn is not None else None,
                max=float(mx) if mx is not None else None,
                avg=float(avg) if avg is not None else None,
                last=float(last) if last is not None else None,
                n=int(n)))

    # Granice resetow: z KAZDEJ probki w zakresie, niezaleznie od koszyka. Wczesniej
    # liczylo sie to tylko w trybie `raw`, wiec kazdy zakres powyzej 6 h (czyli domyslne
    # 24 h) wracal bez ani jednej granicy — a to najwazniejsza linia na tym wykresie.
    sample_rows = (await db.execute(
        select(LimitSample.captured_at, LimitSample.resets_at)
        .where(LimitSample.account_id == acc.id,
               LimitSample.series_id == series_id,
               LimitSample.captured_at >= from_,
               LimitSample.captured_at <= to,
               LimitSample.stale_read.is_(False))
        .order_by(LimitSample.captured_at)
    )).all()

    resets = _reset_boundaries(list(sample_rows))
    gaps = _find_gaps(
        from_=from_, to=to,
        batch_times=(await db.execute(
            select(IngestBatch.received_at)
            .where(IngestBatch.account_id == acc.id,
                   IngestBatch.received_at >= from_, IngestBatch.received_at <= to)
            .order_by(IngestBatch.received_at)
        )).scalars().all(),
        sample_times=[t for t, _ in sample_rows],
        threshold=timedelta(seconds=settings.history_gap_sec),
    )
    return HistoryResponse(bucket=bucket, points=points, resets=resets, gaps=gaps)


@router.get("/events")
async def events(
    level: str | None = None,
    event_type: str | None = Query(None, alias="type"),
    limit: int = Query(200, le=1000),
    user: CurrentUser = Depends(require_authorized_user),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    q = select(IngestEvent).order_by(IngestEvent.id.desc()).limit(limit)
    if level:
        q = q.where(IngestEvent.level == level)
    if event_type:
        q = q.where(IngestEvent.event_type == event_type)
    rows = (await db.execute(q)).scalars().all()
    return [{"id": e.id, "ts": e.ts, "level": e.level, "type": e.event_type,
             "accountId": e.account_id, "batchId": e.batch_id,
             "message": e.message, "detail": e.detail} for e in rows]


@router.get("/batches")
async def batches(
    limit: int = Query(100, le=1000),
    user: CurrentUser = Depends(require_authorized_user),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (await db.execute(
        select(IngestBatch).order_by(IngestBatch.id.desc()).limit(limit)
    )).scalars().all()
    return [{"id": b.id, "receivedAt": b.received_at, "accountId": b.account_id,
             "machineId": b.machine_id, "clientHost": b.client_host,
             "ccVersion": b.cc_version, "hookEvent": b.hook_event,
             "httpStatus": b.http_status, "requestId": b.request_id,
             "rlStatus": b.rl_status, "probeMs": b.probe_ms,
             "samplesWritten": b.samples_written, "ok": b.ok,
             "errorKind": b.error_kind} for b in rows]


@router.get("/batches/{batch_id}/raw")
async def batch_raw(
    batch_id: int,
    user: CurrentUser = Depends(require_authorized_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    b = (await db.execute(select(IngestBatch).where(IngestBatch.id == batch_id))).scalar_one_or_none()
    if b is None or b.raw_payload_id is None:
        raise HTTPException(status_code=404, detail={"reason": "not-found"})
    rp = (await db.execute(select(RawPayload).where(RawPayload.id == b.raw_payload_id))).scalar_one()
    import json
    return {"batchId": b.id, "sha256": rp.sha256, "seenCount": rp.seen_count,
            "body": json.loads(rp.body)}


@router.get("/stats")
async def stats(
    user: CurrentUser = Depends(require_authorized_user),
    db: AsyncSession = Depends(get_session),
) -> dict:
    async def one(q):
        return (await db.execute(q)).scalar_one()

    total_samples = await one(select(func.count()).select_from(LimitSample))
    total_batches = await one(select(func.count()).select_from(IngestBatch))
    raw_rows = await one(select(func.count()).select_from(RawPayload))
    raw_seen = await one(select(func.coalesce(func.sum(RawPayload.seen_count), 0)))
    oldest = await one(select(func.min(LimitSample.captured_at)))
    series_count = await one(select(func.count()).select_from(UsageSeries))
    since = utcnow() - timedelta(hours=24)
    ok24 = await one(select(func.count()).select_from(IngestBatch)
                     .where(IngestBatch.received_at >= since, IngestBatch.ok.is_(True)))
    all24 = await one(select(func.count()).select_from(IngestBatch)
                      .where(IngestBatch.received_at >= since))
    return {
        "totalSamples": total_samples, "totalBatches": total_batches,
        "oldestSampleAt": oldest, "seriesCount": series_count,
        "rawPayloadRows": raw_rows, "rawPayloadSeen": int(raw_seen),
        "rawDedupRatio": (round(1 - raw_rows / int(raw_seen), 4) if raw_seen else None),
        "ingestSuccessRate24h": (round(ok24 / all24, 4) if all24 else None),
    }
