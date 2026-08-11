"""Composing /api/status — one cheap query over series_state, not a groupwise-max.

This is where the project's most important rule comes together: the `unknown` state MUST
NOT be rendered as 0%. That is why `utilization` is None there, with `raw_utilization`
riding alongside it carrying the last MEASURED value — so the UI can show "last seen 42%,
but not what it is right now" without pretending it is a current measurement.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.freshness import display_utilization, freshness
from app.models import Account, IngestBatch, LimitSample, SeriesState, UsageSeries
from app.parsing import Sample, meter_withdrawn, window_start_index
from app.schemas import AccountStatus, SeriesStatus, StatusResponse
from app.services.cascade import SeriesFacts, build_cascade
from app.services.ingest import utcnow

# v2: wire time carries an offset (it did not before), kind/group/bucketKey in series,
# cascade[] on the account, gaps[] in two flavors.
# v3: confirmedAt + valueSince in series — freshness counts from CONFIRMATION, not from the
#     sample write. Without it dedup makes an unchanged value look like lost connectivity.
# A bump = an update to docs/API.md.
# deltaFrom arrived AFTER v3 and does NOT bump the version — adding a field does not break
# compatibility.
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
    """The account's series over the last hour, in one query — the delta baseline has to be
    clipped to the current window, so a single row would not be enough anyway."""
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


async def _last_measured(db: AsyncSession, account_id: int,
                         series_id: int) -> tuple[float, dict | None, datetime] | None:
    """The last sample of this series that was a REAL measurement — value, amounts and time.

    Needed only for series with a withdrawn meter. The current state alone cannot give it:
    `series_state` holds the LAST write, and with the gate closed the last write is exactly
    the absence of a measurement — with the amounts zeroed by the withdrawal payload.
    Without this lookup the row loses the only number that says anything about usage, and
    that is a loss of information, not protection from it (rule 4: show the last MEASURED
    percent, not a zero).

    The condition lives entirely in SQL, because "measurement" has one meaning here: a
    non-empty value that the guard did not judge to have gone backwards. A withdrawn meter
    does NOT write a value (`parse_usage` returns None), so a row with a value is by
    definition a row from before the block. One hit on `ix_samples_series_time`, and only
    for series that have a reason — in practice zero or two per account.
    """
    row = (await db.execute(
        select(LimitSample.utilization, LimitSample.extra, LimitSample.captured_at)
        .where(LimitSample.account_id == account_id,
               LimitSample.series_id == series_id,
               LimitSample.utilization.is_not(None),
               LimitSample.stale_read.is_(False))
        .order_by(LimitSample.captured_at.desc())
        .limit(1)
    )).first()
    return (float(row[0]), row[1], row[2]) if row is not None else None


def _delta_1h(rows: Sequence[Sample], *, now: datetime, current: float | None,
              resets_at: datetime | None) -> tuple[float | None, datetime | None]:
    """The rise in usage and the time of the sample it is counted from.

    Baseline CLIPPED TO THE CURRENT WINDOW: without it, right after a reset the reference
    point was a sample from the previous window and the UI wrote "-46 pp in the last hour"
    for a whole hour.
    """
    if current is None:
        return None, None
    if resets_at is not None and now > resets_at:
        # Every sample belongs to the PREVIOUS window. No tolerance in the condition,
        # exactly as in freshness() — otherwise one card would show "~0%" and a delta
        # from the old window.
        return None, None
    i = window_start_index(rows, settings.reset_window_eps_sec, settings.monotonic_eps)
    in_window = [(t, u) for t, u, _ in rows[i:] if u is not None]
    if len(in_window) < 2:
        return None, None      # one sample is not a span
    t0, u0 = in_window[0]
    return round(current - u0, 4), t0


def _mark_duplicates(series: list[SeriesStatus]) -> None:
    """The API reports the same limits twice: `five_hour` and `limits[kind=session]`,
    `seven_day` and `limits[kind=weekly_all]`, `seven_day_<model>` and `weekly_scoped`.

    We do not map them rigidly — hardcoding the set of buckets already turned out wrong once
    (5 of 17 keys were unknown). Instead we pair them by DATA: an identical
    (utilization, resets_at) means the same limit. The entry from `limits[]` wins, because it
    carries `is_active` and `severity`, which a bucket does not have.

    When the values drift apart — and the reference repo describes newer responses zeroing
    the older per-model fields — the pairs simply will not form and both series stay visible.
    That is the right behavior: better to show the divergence than to hide it.

    THE SECOND PAIR is of a different kind, and that is why it has its own loop instead of a
    loosened first condition: `spend` and `extra_usage` are two VIEWS OF THE SAME credit
    pool, not two limits.
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

    # Here we pair by SOURCE IDENTITY, not by value as above — and this is not an exception
    # to that rule, only a consequence of the fact that these two series CANNOT agree
    # numerically: `spend.percent` is rounded to an int (93), `extra_usage.utilization`
    # carries full precision (92.656). Pairing by data would never catch them.
    # No bucket name appears here (rule 5) — `source` is an enum of the contract.
    #
    # `spend` wins, because it carries the amounts in a monetary type and `severity`. The
    # loser STAYS in the response and must not be removed from here: its `utilization` is
    # the only precise copy of that number, and `extra` the only place where the UI sees
    # `spend_limit_reached`, `user_disabled` and `credits_ever_enabled`. Only its ROW goes
    # dark, because the UI draws `primary`.
    #
    # The cascade is immune to this for a separate reason: `facts` are collected BEFORE this
    # filter (see `build_account_status`), so `build_cascade` reads `source="extra_usage"`
    # no matter what is set here — even for a series that never made it into `series[]`.
    #
    # No partner => stays visible. The same rule as above: better to show the divergence
    # than to hide it.
    spend = next((s for s in series if s.source == "spend"), None)
    if spend is not None:
        for s in series:
            if s.source == "extra_usage":
                s.primary = False
                s.duplicate_of = spend.series_key


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
        # The reason is rebuilt from `last_extra` rather than from a separate column:
        # `enabled` and `disabled_reason` are already there, so state written before this
        # change reads back correctly and no migration is needed.
        reason = meter_withdrawn(st.last_extra)
        # With a withdrawn meter the WHOLE row describes the last real measurement: its
        # value, its amounts and its time. The withdrawal payload carries none of those
        # things — it has `used` zeroed, `limit: null` and `percent: 0`.
        measured = await _last_measured(db, a.id, s.id) if reason else None
        raw_all = measured[0] if measured else (None if reason else raw_all)
        extra = measured[1] if measured else st.last_extra

        # Facts for the cascade are collected BEFORE the view filter: `extra:usage` on an
        # account without credits has utilization = null and is about to drop out, and the
        # cascade reads from it. Amounts come from the last measurement, so the cascade shows
        # WHERE the work stopped; `enabled` in that `extra` still says `true`, but the reason
        # outranks it (`cascade.build_cascade`) — otherwise a closed gate would look open.
        facts.append(SeriesFacts(
            series_key=s.series_key, source=s.source, kind=s.kind,
            bucket_key=s.bucket_key, utilization=raw_all,
            is_active=st.last_is_active, extra=extra,
            unavailable_reason=reason,
        ))

        # Series that never had a value (e.g. seven_day_opus on an account without Opus)
        # are registered, but they do not clutter the view.
        if not s.ever_non_null and st.last_utilization is None:
            continue

        # Fallback to last_captured_at for rows predating the migration that added
        # last_confirmed_at — for those the two meanings are identical anyway.
        confirmed = st.last_confirmed_at or st.last_captured_at
        captured, value_since = st.last_captured_at, st.value_since
        if measured:
            # The timestamps describe the NUMBER that appears in the row. That number was last
            # confirmed before the meter was withdrawn and nobody has confirmed it since —
            # after that the only thing confirmed was that there is no measurement. Leaving
            # a fresh `confirmed_at` here would mean "measured a moment ago" and would be
            # exactly the false certainty this whole tool defends against.
            captured = confirmed = value_since = measured[2]
        state = freshness(
            now=now,
            confirmed_at=confirmed,
            resets_at=st.last_resets_at,
            last_batch_at=lb,
            fresh_window_sec=settings.fresh_window_sec,
            client_silent_sec=settings.client_silent_sec,
        )
        raw_u = raw_all
        # `utilization` is the CURRENT number, and with a withdrawn meter there is no current
        # one — not even when the last measurement is a minute old and `freshness` says
        # `live`. `rawUtilization` stays, because it is the last MEASURED percent, and that
        # is all that is known about usage. The UI computes `utilization ?? rawUtilization`,
        # so it sees exactly that — with the reading's age and the reason beside it, never as
        # a current measurement.
        shown = None if reason else display_utilization(state, raw_u)
        secs = (int((st.last_resets_at - now).total_seconds())
                if st.last_resets_at is not None else None)
        # `shown`, not `raw_u`: the delta accompanies the number visible on screen, and
        # with `unknown` there is no number.
        d_pct, d_from = _delta_1h(recent.get(s.id, ()), now=now, current=shown,
                                  resets_at=st.last_resets_at)

        series.append(SeriesStatus(
            series_id=s.id, series_key=s.series_key, label=s.display_label,
            source=s.source, sort_order=s.sort_order,
            kind=s.kind, group=s.group_key, bucket_key=s.bucket_key,
            utilization=shown, raw_utilization=raw_u, unavailable_reason=reason,
            resets_at=st.last_resets_at, seconds_to_reset=secs,
            captured_at=captured, confirmed_at=confirmed,
            value_since=value_since, freshness=state,
            is_active=st.last_is_active, severity=st.last_severity,
            delta_pct_1h=d_pct, delta_from=d_from,
            extra=extra,
        ))

    _mark_duplicates(series)

    # There used to be a warning here: "some series on account X are in the unknown state".
    # It went away together with the very notion of `unknown` in the UI: the view now shows
    # the last MEASURED percent, and how much it is worth is told by the reading-age label
    # next to each series. So the banner restated with a state name what was already shown
    # beside it as a number of minutes. `warnings[]` stays in the contract — nobody fills
    # it today, and that is the correct state, not a missing implementation.

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
