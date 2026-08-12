"""Writing a measurement: validation, series registration, dedup, monotonicity guard, state.

Three mechanisms that are not obvious and without which the system gives bad data:

1. DEDUP. The probe polls every ~120 s, and utilization changes rarely. Without dedup the
   table swells with identical rows. We write a new row only when (utilization, resets_at)
   have changed OR the heartbeat has elapsed. Lossless, because between the points of change
   the value is constant by definition, and ingest_batches proves the client was alive anyway.

2. MONOTONICITY GUARD. When the same account runs on two machines, each one has its OWN
   token cache and its own moment of polling. The machine with the older reading may send
   a lower value than we already know. A naive write would move the current state back and
   it would look like a window reset. The rule: with a PROVABLY identical window (both
   boundaries known and matching within tolerance) we write a drop > MONOTONIC_EPS with the
   stale_read flag, but we do NOT touch series_state. The mere absence of a boundary on both
   sides is NOT proof and must not hold the write back — see parsing.known_same_reset_window.

3. STATE IS UPDATED ONLY BY THE NEWEST SAMPLE. This also protects against a backlog which
   after a break of many hours pours in old samples — those must not overwrite the current
   state. The state also holds the window boundary, and a measurement without a boundary
   does NOT clear it as long as that boundary has not passed yet — see
   parsing.carry_reset_window.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Account, IngestBatch, IngestEvent, LimitSample, Machine, MachineAccount,
    RawPayload, SeriesState, UsageSeries,
)
from app.parsing import (
    Observation, carry_reset_window, known_same_reset_window, meter_withdrawn, parse_ts,
    parse_usage, same_reset_window,
)

# Zero — used when the payload does not carry `sent_at` (a probe below v5 or a foreign client).
# Graceful degradation: the client stamp then goes through untouched, exactly as before the
# change.
_NO_OFFSET = timedelta(0)

_ACCOUNT_FIELDS = {
    "email": "email", "display_name": "display_name", "org_uuid": "org_uuid",
    "org_name": "org_name", "org_type": "org_type", "seat_tier": "seat_tier",
    "org_rate_limit_tier": "org_rate_limit_tier",
    "user_rate_limit_tier": "user_rate_limit_tier",
    "extra_usage_enabled": "extra_usage_enabled",
}
# Plan fields — a change here is worth an event, because it changes what the percentages mean.
_PLAN_FIELDS = ("org_type", "seat_tier", "org_rate_limit_tier",
                "user_rate_limit_tier", "subscription_type")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _event(db: AsyncSession, *, level: str, event_type: str, message: str | None = None,
                 account_id: int | None = None, batch_id: int | None = None,
                 detail: dict | None = None) -> None:
    db.add(IngestEvent(level=level, event_type=event_type, message=message,
                       account_id=account_id, batch_id=batch_id, detail=detail))


async def get_or_create_machine(db: AsyncSession, name: str, client: dict,
                                is_backlog: bool = False) -> Machine:
    m = (await db.execute(select(Machine).where(Machine.name == name))).scalar_one_or_none()
    if m is None:
        m = Machine(name=name)
        db.add(m)
        await db.flush()
    m.host = client.get("host") or m.host
    # Script version ONLY from the current record. Backlog entries sit in the spool for hours
    # or days and carry a pre-update version — taken here they would move this field back.
    if not is_backlog:
        m.script_version = client.get("script_version") or m.script_version
    m.last_seen_at = utcnow()
    m.batches = (m.batches or 0) + 1
    return m


async def get_or_create_account(db: AsyncSession, acct: dict,
                                token_meta: dict) -> tuple[Account, bool]:
    uuid = acct.get("uuid")
    a = (await db.execute(select(Account).where(Account.account_uuid == uuid))).scalar_one_or_none()
    created = False
    if a is None:
        a = Account(account_uuid=uuid, label=acct.get("email") or uuid[:8])
        db.add(a)
        await db.flush()
        created = True

    before = {f: getattr(a, f) for f in _PLAN_FIELDS}
    for src, dst in _ACCOUNT_FIELDS.items():
        v = acct.get(src)
        if v is not None:
            setattr(a, dst, v)
    if token_meta.get("subscription_type"):
        a.subscription_type = token_meta["subscription_type"]
    after = {f: getattr(a, f) for f in _PLAN_FIELDS}

    if not created and before != after:
        changed = {k: [before[k], after[k]] for k in before if before[k] != after[k]}
        await _event(db, level="info", event_type="plan_changed", account_id=a.id,
                     message="Account plan fields changed", detail=changed)
    return a, created


async def get_or_create_series(db: AsyncSession, o: Observation,
                               cache: dict[str, UsageSeries]) -> tuple[UsageSeries, bool]:
    if o.series_key in cache:
        return cache[o.series_key], False
    s = (await db.execute(
        select(UsageSeries).where(UsageSeries.series_key == o.series_key)
    )).scalar_one_or_none()
    created = False
    if s is None:
        s = UsageSeries(
            series_key=o.series_key, source=o.source, bucket_key=o.bucket_key,
            kind=o.kind, group_key=o.group_key,
            model_display_name=o.model_display_name,
            surface_display_name=o.surface_display_name,
            display_label=o.display_label, sort_order=o.sort_order,
        )
        db.add(s)
        await db.flush()
        created = True
    s.last_seen_at = utcnow()
    # The label and the ordering are DESCRIPTION, not data — we refresh them on every
    # measurement. Without this, a fix to the label dictionary (or a new model name in scope)
    # would never reach series registered earlier and the UI would show the old content until
    # the end of the database's life.
    if s.display_label != o.display_label:
        s.display_label = o.display_label
    if s.sort_order != o.sort_order:
        s.sort_order = o.sort_order
    if o.utilization is not None:
        s.ever_non_null = True
    cache[o.series_key] = s
    return s, created


async def store_raw(db: AsyncSession, usage: Any) -> tuple[RawPayload, str]:
    """Content-addressed — while idle the response is byte-identical, so instead of
    thousands of copies we have one row and a counter."""
    body, digest = _canonical(usage)
    rp = (await db.execute(select(RawPayload).where(RawPayload.sha256 == digest))).scalar_one_or_none()
    now = utcnow()
    if rp is None:
        rp = RawPayload(sha256=digest, body=body, last_seen_at=now, seen_count=1)
        db.add(rp)
        await db.flush()
    else:
        rp.last_seen_at = now
        rp.seen_count = (rp.seen_count or 0) + 1
    return rp, digest


# BELOW its caller on purpose: `store_raw` is referenced by line number from
# routers/ingest.py, and inserting above it would move it.
def _canonical(obj: Any) -> tuple[str, str]:
    """The canonical body and its sha256 — ONE recipe, because these bytes ADDRESS ROWS
    ALREADY IN THE DATABASE. Change the formula and `raw_payloads` opens a second row for a
    payload it already holds, while the backlog idempotence key in `_write_observation` —
    which compares a digest computed now against one written months ago — stops matching and
    lets a replay duplicate the sample. One home, so that a change is a decision."""
    body = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return body, hashlib.sha256(body.encode("utf-8")).hexdigest()


def measured_at(ts: datetime | None, offset: timedelta,
                arrived_at: datetime) -> datetime | None:
    """SERVER-SIDE TIMESTAMPING. The client gives the age; the timestamp is `received_at - age`.

        offset      = arrived_at - sent_at            (once per request)
        measured_at = min(ts + offset, arrived_at)

    Expanded: `arrived_at - (sent_at - ts)`. The client's WALL clock does not enter the
    calculation — only the difference `sent_at - ts` counts, and that one is within a single
    clock and therefore trustworthy. The same formula handles entries from the spool: their
    `sent_at` comes from the moment of the failed attempt, so the age works out on its own and
    nothing has to be recalculated on the client side.

    Clamping to `arrived_at` guarantees the measurement is not newer than the moment of receipt.

    None goes in and None comes out: a missing time is IGNORANCE, not "now". The previous
    version (`resolve_captured_at`) substituted the server time here in three cases — that is,
    it claimed the value had been measured a moment ago. For an entry eight days old that was
    the opposite of protection: such a measurement became `newest` and took over the current
    state."""
    if ts is None:
        return None
    shifted = ts + offset
    return shifted if shifted < arrived_at else arrived_at


def request_offset(payload: dict, arrived_at: datetime) -> timedelta:
    """`arrived_at - measurement.sent_at` — one per whole request, from the OUTER record.

    Backlog entries ride in the same envelope, so their age (`sent_at - ts`, in the client's
    OWN clock) is corrected by the same amount; their own `sent_at` they use only for the
    check "the measurement was not taken after it was sent".

    Here, and not in the handler, so that the tests compute the offset THE SAME WAY the real
    path does — otherwise a divergence between them would show up only live."""
    meas = payload.get("measurement")
    sent = parse_ts(meas.get("sent_at")) if isinstance(meas, dict) else None
    return (arrived_at - sent) if sent is not None else _NO_OFFSET


async def _write_observation(
    db: AsyncSession, *, account: Account, series: UsageSeries, o: Observation,
    captured_at: datetime, client_captured_at: datetime | None,
    batch: IngestBatch, session_id: str | None, source: str,
    is_backlog: bool = False, client_reason: str | None = None,
) -> tuple[bool, bool]:
    """Returns (written, stale_read)."""
    if is_backlog:
        # IDEMPOTENCE OF A SPOOL REPLAY. The probe truncates the spool only after the
        # response, so when the response is lost (timeout, broken connection) the same
        # entries arrive again. Without this guard `changed` below compares against the
        # CURRENT state, which has moved on since that measurement — a replay therefore
        # comes out as a "change" and appends a SECOND row for the same measurement. The
        # database will not stop it: there is no UNIQUE on
        # (account_id, series_id, client_captured_at), only an ordinary index.
        #
        # The key: (account, series, client_captured_at, machine, payload sha256).
        #   - `client_captured_at`, and NOT `captured_at`: since timestamping moved to the server
        #     side, `captured_at` is a function of the REQUEST (it holds the `offset` computed
        #     from that request's `arrived_at`), so a replay of the same entry in another
        #     request gets a different value and the guard would not see it. The raw client
        #     time is a function of the entry itself and on a replay it is identical — it is
        #     the only part that does not change.
        #   - the machine, because the same account from TWO machines is correctly two
        #     measurements;
        #   - sha256, because `parse_ts` truncates the time to whole seconds, and parallel
        #     hooks can fire two probes within the same second. Without it a collision on
        #     time alone would erase the second, DIFFERENT measurement. With it only the
        #     byte-identical case collapses, that is the same measurement — and collapsing
        #     the same measurement is exactly what the dedup below does.
        #     `batch.payload_sha256` is set before the observation loop, so here it is
        #     never NULL.
        #
        # Per OBSERVATION, not once per entry: dedup is per series, so the original write
        # could have skipped series 1 and written series 3. The shortcut "check the first
        # observation and leave" would then let a duplicate of series 3 through. The cost is
        # one seek on ix_samples_series_client_time per observation (~7 per entry, up to
        # ~1400 at the maximum backlog) — entirely from the index, returning 0 or 1 rows.
        already = None if client_captured_at is None else (await db.execute(
            select(LimitSample.id)
            .join(IngestBatch, IngestBatch.id == LimitSample.batch_id)
            .where(LimitSample.account_id == account.id,
                   LimitSample.series_id == series.id,
                   LimitSample.client_captured_at == client_captured_at,
                   IngestBatch.machine_id == batch.machine_id,
                   IngestBatch.payload_sha256 == batch.payload_sha256)
            .limit(1)
        )).scalar_one_or_none()
        if already is not None:
            # (False, False), not an exception: this is a CORRECT result — we have this
            # measurement. The entry must be counted in `accepted` so the probe truncates it.
            return False, False

    st = (await db.execute(
        select(SeriesState).where(SeriesState.account_id == account.id,
                                  SeriesState.series_id == series.id)
    )).scalar_one_or_none()

    # A meter's withdrawal and its return are an EVENT, not just a change of number: for
    # `spend` and `extra:usage` it is the only moment the log shows that the organization shut
    # the gate. Only on the TRANSITION and only when there is something to compare against —
    # the first measurement in a series' life changes nothing, so there is nothing to report.
    if st is not None:
        was = meter_withdrawn(st.last_extra)
        if o.unavailable_reason and not was:
            await _event(db, level="warn", event_type="meter_withdrawn",
                         account_id=account.id, batch_id=batch.id,
                         message="Meter %s withdrawn by the organization" % series.series_key,
                         detail={"series": series.series_key,
                                 "reason": o.unavailable_reason,
                                 # Reason from the CLIENT cache
                                 # (`cachedExtraUsageDisabledReason`) — for inspection only.
                                 # The verdict rests on the in-band data, because only that
                                 # is consistent with the rest of the same response.
                                 "client_reason": client_reason,
                                 "last_utilization": (float(st.last_utilization)
                                                      if st.last_utilization is not None
                                                      else None)})
        elif was and not o.unavailable_reason:
            await _event(db, level="info", event_type="meter_restored",
                         account_id=account.id, batch_id=batch.id,
                         message="Meter %s is working again" % series.series_key,
                         detail={"series": series.series_key, "was_reason": was,
                                 "client_reason": client_reason,
                                 "utilization": o.utilization})

    stale_read = False
    should_write = True
    changed = True
    newest = st is None or st.last_captured_at is None or captured_at > st.last_captured_at

    prev_r = st.last_resets_at if st is not None else None
    # The boundary that WILL ENTER the state. A measurement without a boundary does not clear
    # a boundary that has not passed yet — see parsing.carry_reset_window. Carrying it over
    # counts only for the sample that really writes the state (`newest`); a backlog sample
    # does not touch the state, so for it the comparison must go against what arrived.
    next_r = carry_reset_window(prev_r, o.resets_at, captured_at) if newest else o.resets_at

    if st is not None and st.last_captured_at is not None:
        prev_u = float(st.last_utilization) if st.last_utilization is not None else None
        # WITH TOLERANCE, not on equality: the window boundary reported by Anthropic wobbles by
        # ~2 s, so a literal comparison was always false and silently switched off both the
        # dedup and the monotonicity guard below.
        #
        # Dedup asks "WILL THE STATE CHANGE", so it compares `prev_r` with `next_r`, not with
        # the raw `o.resets_at`. Otherwise, with a carried-over boundary EVERY subsequent
        # measurement without a boundary would look like a change (a known boundary in the
        # state vs NULL in the measurement) and dedup would write a row per measurement —
        # exactly what it must not do.
        changed = (prev_u != o.utilization
                   or not same_reset_window(prev_r, next_r,
                                            settings.reset_window_eps_sec))

        # (2) monotonicity guard — an out-of-date reading from another machine.
        #
        # `known_same_reset_window`, and NOT `same_reset_window`: the guard holds back the
        # state write, so it may fire only on PROOF that the window is the same. Two NULLs are
        # an absence of proof, and taken for proof they froze the state after every session
        # reset and PERMANENTLY for series that never have a boundary (`spend:org`,
        # `extra:usage`).
        #
        # What goes into the guard is `o.resets_at`, that is what the measurement SAID — not
        # `next_r`. The carried-over boundary is our INFERENCE and used here it would
        # reproduce the same freeze: every measurement without a boundary would get "the same
        # window" back.
        if (known_same_reset_window(prev_r, o.resets_at, settings.reset_window_eps_sec)
                and prev_u is not None and o.utilization is not None
                and o.utilization < prev_u - settings.monotonic_eps):
            stale_read = True

        if not changed:
            # (1) dedup: we write only the heartbeat
            age = (captured_at - st.last_captured_at).total_seconds()
            should_write = age >= settings.sample_heartbeat_sec

    if not should_write:
        # We do not write the sample, but THE MEASUREMENT DID HAPPEN. Without this a skipped
        # write is indistinguishable from client silence and the UI reports a false data age.
        if newest and not stale_read and st is not None:
            st.last_confirmed_at = captured_at
        return False, False

    # `o.resets_at`, not `next_r`: the SAMPLE is a record of the measurement and holds what the
    # measurement gave. Carrying the boundary over belongs to the state (`series_state` is a
    # rebuildable cache, not a fact), and `window_start_index` reads exactly the samples and
    # rests the `passed` signal on the absence of a boundary — writing our inference there
    # would blind reset detection in the history.
    sample = LimitSample(
        account_id=account.id, series_id=series.id, captured_at=captured_at,
        client_captured_at=client_captured_at,
        batch_id=batch.id, source=source, utilization=o.utilization,
        resets_at=o.resets_at, is_active=o.is_active, severity=o.severity,
        stale_read=stale_read, session_id=session_id,
        extra=o.extra or None,
    )
    db.add(sample)
    await db.flush()

    # (3) the state is updated only by the newest, unsuspicious sample
    if newest and not stale_read:
        if st is None:
            st = SeriesState(account_id=account.id, series_id=series.id)
            db.add(st)
        st.prev_utilization = st.last_utilization
        st.prev_captured_at = st.last_captured_at
        st.last_sample_id = sample.id
        st.last_captured_at = captured_at
        st.last_confirmed_at = captured_at
        # We move value_since ONLY on a change of value — otherwise a heartbeat write (the
        # same value, a new row) would pose as a change and "unchanged since" would reset
        # every heartbeat, that is, it would show nonsense.
        if changed or st.value_since is None:
            st.value_since = captured_at
        st.last_utilization = o.utilization
        st.last_resets_at = next_r
        st.last_is_active = o.is_active
        st.last_severity = o.severity
        st.last_extra = o.extra or None
    return True, stale_read


async def ingest_one(db: AsyncSession, *, machine_name: str, payload: dict,
                     arrived_at: datetime | None = None,
                     offset: timedelta = _NO_OFFSET,
                     is_backlog: bool = False) -> dict:
    """Processes one measurement. Never throws on data — a problem is recorded as an event
    and we return how much we managed to write.

    `arrived_at` is the TIME ANCHOR: the moment the request was received, taken in the handler
    BEFORE the write lock. It must not be computed here — `ingest_one` already runs under
    `_WRITE_LOCK`, so a request that waited out somebody else's backlog would timestamp the
    measurements that much too fresh, and every backlog entry would get its own, different
    anchor. The default is there for the tests alone.

    `offset = arrived_at - sent_at` is common to the whole request — see `measured_at`."""
    if arrived_at is None:
        arrived_at = utcnow()
    now = arrived_at
    client = payload.get("client") or {}
    hook = payload.get("hook") or {}
    meas = payload.get("measurement") or {}
    # token_meta is OPTIONAL from probe version 3 on: on macOS the credentials sit in the
    # Keychain and the measurement no longer depends on them — only the plan tags are missing
    # then, not the data.
    token_meta = payload.get("token_meta") or {}
    acct = payload.get("account") or {}
    usage = payload.get("usage")

    machine = await get_or_create_machine(db, machine_name, client, is_backlog)

    source = meas.get("source") or "probe"
    if source not in ("probe", "cli_merged", "cli_usage_cache"):
        source = "cli_usage_cache"      # unknown label: we do not fail the write over an enum

    batch = IngestBatch(
        received_at=now, machine_id=machine.id,
        client_host=client.get("host"), config_dir_hash=client.get("config_dir_hash"),
        script_version=client.get("script_version"),
        hook_event=hook.get("event"), session_id=hook.get("session_id"),
        measurement_source=source, cache_age_s=meas.get("cache_age_s"),
        fresh_age_s=meas.get("fresh_age_s"), probe_ms=client.get("exec_ms"),
        # Without this the computed `captured_at` cannot be reconstructed from the database:
        # we know `received_at` and the raw client time, but not how far we shifted them.
        clock_offset_s=int(offset.total_seconds()),
    )
    db.add(batch)
    await db.flush()

    if not acct.get("uuid"):
        batch.ok = False
        batch.error_kind = "no_account"
        await _event(db, level="warn", event_type="no_oauth_account", batch_id=batch.id,
                     message="Payload without account.uuid — measurement rejected")
        return {"samples_written": 0, "batch_id": batch.id, "ok": False,
                "account_uuid": None}

    account, created = await get_or_create_account(db, acct, token_meta)
    batch.account_id = account.id
    account.last_client_host = client.get("host") or account.last_client_host

    if created:
        await _event(db, level="info", event_type="account_created", account_id=account.id,
                     batch_id=batch.id, message="New account detected",
                     detail={"email": acct.get("email"), "org_type": acct.get("org_type")})

    # the (machine, account) pair — detection instead of prohibition
    ma = (await db.execute(
        select(MachineAccount).where(MachineAccount.machine_id == machine.id,
                                     MachineAccount.account_id == account.id)
    )).scalar_one_or_none()
    if ma is None:
        db.add(MachineAccount(machine_id=machine.id, account_id=account.id,
                              last_seen_at=now, samples=0))
        await _event(db, level="info", event_type="new_account_for_token",
                     account_id=account.id, batch_id=batch.id,
                     message="Machine %s reports this account for the first time" % machine_name)
    else:
        ma.last_seen_at = now
        ma.samples = (ma.samples or 0) + 1

    # an account switch on this machine — that is, /login
    prev = (await db.execute(
        select(IngestBatch).where(IngestBatch.machine_id == machine.id,
                                  IngestBatch.id != batch.id,
                                  IngestBatch.account_id.is_not(None))
        .order_by(IngestBatch.id.desc()).limit(1)
    )).scalar_one_or_none()
    if prev is not None and prev.account_id != account.id:
        await _event(db, level="info", event_type="account_switched", account_id=account.id,
                     batch_id=batch.id, message="Account switched on machine %s" % machine_name,
                     detail={"from_account_id": prev.account_id, "to_account_id": account.id})

    if not isinstance(usage, dict):
        batch.ok = False
        batch.error_kind = "no_usage"
        await _event(db, level="warn", event_type="parse_error", account_id=account.id,
                     batch_id=batch.id, message="No usage object in the payload")
        # account_uuid despite ok=False: the batch WAS assigned to an account, so
        # `last_batch_at` moved — and that is precisely what lets `freshness()` tell
        # "the client was silent" apart from "the client was alive and there is still no
        # sample" (unknown). This frame can flip a series into the failure state and must
        # reach subscribers.
        return {"samples_written": 0, "batch_id": batch.id, "ok": False,
                "account_uuid": account.account_uuid}

    # `usage_raw` — the cache block before the probe merged and sanitized it — from probe
    # version 15 on. THAT is what rule 6 means by "the raw response": `usage` is our own
    # edit of it, with every boundary `sanitize` refused already zeroed, and this table is
    # the only place the original could survive. Older probes (and the whole fleet, until
    # someone cuts a release) do not send the field, and for them the merged object stays
    # the best available answer — falling back to it is not a compromise, it is all there is.
    raw = payload.get("usage_raw")
    rp, _ = await store_raw(db, raw if isinstance(raw, dict) else usage)
    batch.raw_payload_id = rp.id
    # NOT the archive's digest. This field is the discriminator in the backlog idempotence
    # key (`_write_observation`), and the cache block is IDENTICAL for two measurements that
    # differ only in a fresh `/usage` dump — keyed on it, the second one collapses into the
    # first and a replay silently drops it. The digest has to cover exactly what the parser
    # reads, so it is taken over `usage`, by `store_raw`'s own recipe: entries ingested by
    # earlier versions were keyed the same way and must keep matching on a replay.
    _, batch.payload_sha256 = _canonical(usage)

    # TWO TIMES IN ONE PAYLOAD. `captured_at` is the time of the Claude Code cache and applies
    # to everything; `measurement.fresh_at` is the time of the `/usage` dump and applies ONLY
    # to the series listed in `fresh_covered`. The cache is sometimes an hour older than the
    # dump, and `spend` and `extra_usage` always come from the CACHE — one stamp for both sources
    # made them younger by exactly that difference.
    cache_ts = parse_ts(payload.get("captured_at"))
    fresh_ts = parse_ts(meas.get("fresh_at"))
    sent_ts = parse_ts(meas.get("sent_at"))

    # `frozenset(...)` on a raw field from the network would throw TypeError on `None` and on
    # every non-hashable element, and `ingest_one` does NOT have a try/except despite the
    # promise in its docstring: for the current record that is a 500 on the whole request, for
    # a backlog entry a `break` and a PERMANENTLY blocked tail of the spool. The endpoint is
    # exposed on the internet.
    raw_covered = meas.get("fresh_covered")
    fresh_covered = frozenset(
        x for x in raw_covered if isinstance(x, str)
    ) if isinstance(raw_covered, list) else frozenset()

    # THE MEASUREMENT CANNOT HAVE BEEN TAKEN AFTER IT WAS SENT. If it was, the client clock
    # moved back between the write and the send and this entry's timestamp is uncertain — and
    # the expansion shows this is exactly the clamping condition (`ts + offset > arrived_at` <=>
    # `ts > sent_at`), so the entry would land on `arrived_at`, pass `newest` and overwrite
    # the state with an old measurement. We reject the whole thing; the raw payload is already
    # in the database (rule 6), the entry goes to `accepted` so the probe truncates the spool.
    # The criterion applies only to entries carrying `sent_at`.
    if sent_ts is not None and any(t is not None and t > sent_ts
                                   for t in (cache_ts, fresh_ts)):
        batch.ok = False
        batch.error_kind = "clock_backwards"
        await _event(db, level="warn", event_type="clock_backwards", account_id=account.id,
                     batch_id=batch.id,
                     message="Measurement dated AFTER it was sent — client clock moved back",
                     detail={"captured_at": payload.get("captured_at"),
                             "fresh_at": meas.get("fresh_at"),
                             "sent_at": meas.get("sent_at")})
        return {"samples_written": 0, "batch_id": batch.id, "ok": False,
                "account_uuid": account.account_uuid}

    # DIAGNOSTICS, with no effect on the write. The timestamping rests on the difference
    # `sent_at - ts`, which is within a single clock — a divergence against the server no
    # longer corrupts it.
    if abs(offset.total_seconds()) > settings.clock_skew_tolerance_sec:
        await _event(db, level="warn", event_type="clock_skew", account_id=account.id,
                     batch_id=batch.id,
                     message="Client clock skewed by %ds" % int(offset.total_seconds()),
                     detail={"sent_at": meas.get("sent_at"),
                             "arrived_at": arrived_at.isoformat()})

    parsed = parse_usage(usage, fresh_covered)
    if parsed.problems:
        await _event(db, level="warn", event_type="schema_drift", account_id=account.id,
                     batch_id=batch.id, message="Unexpected response shape",
                     detail={"problems": parsed.problems})

    cache: dict[str, UsageSeries] = {}
    written = stale = undated = 0
    newest_at: datetime | None = None
    new_series: list[str] = []
    for o in parsed.observations:
        client_ts = fresh_ts if (o.covered_by_fresh and fresh_ts is not None) else cache_ts
        captured_at = measured_at(client_ts, offset, arrived_at)
        if captured_at is None:
            # `limit_samples.captured_at` is NOT NULL, so without this path it would be an
            # IntegrityError on the whole request instead of skipping one observation.
            undated += 1
            continue
        if newest_at is None or captured_at > newest_at:
            newest_at = captured_at
        series, s_created = await get_or_create_series(db, o, cache)
        if s_created:
            new_series.append(o.series_key)
        w, sr = await _write_observation(
            db, account=account, series=series, o=o, captured_at=captured_at,
            client_captured_at=client_ts,
            batch=batch, session_id=hook.get("session_id"), source=source,
            is_backlog=is_backlog,
            # A plain `get`: a probe below version 4 does not send this field and that is a
            # correct state, not missing data.
            client_reason=meas.get("extra_usage_disabled_reason"),
        )
        written += int(w)
        stale += int(sr)

    if undated:
        await _event(db, level="warn", event_type="no_captured_at", account_id=account.id,
                     batch_id=batch.id,
                     message="%d observations without a measurement time — skipped" % undated,
                     detail={"captured_at": payload.get("captured_at"),
                             "fresh_at": meas.get("fresh_at")})

    # `max`, not an assignment: backlog entries come AFTER the current record, so emptying
    # the spool would move this field back by hours.
    if newest_at is not None and (account.last_sample_at is None
                                  or newest_at > account.last_sample_at):
        account.last_sample_at = newest_at

    if new_series:
        await _event(db, level="info", event_type="series_registered", account_id=account.id,
                     batch_id=batch.id, message="New series registered",
                     detail={"series": new_series})
    if stale:
        await _event(db, level="info", event_type="stale_read", account_id=account.id,
                     batch_id=batch.id,
                     message="%d stale readings — current state untouched" % stale)

    batch.samples_written = written
    return {"samples_written": written, "batch_id": batch.id, "ok": True,
            "account_uuid": account.account_uuid,
            "series_registered": new_series, "stale_reads": stale}
