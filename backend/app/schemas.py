"""DTOs. camelCase on the outside, snake_case on the inside.

TIME ON THE WIRE IS ALWAYS UTC WITH AN OFFSET (`...Z`) — see `UtcDt`. The database columns
are naive and stay that way; the zone is attached at serialization only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, PlainSerializer


def _camel(s: str) -> str:
    head, *rest = s.split("_")
    return head + "".join(w.capitalize() for w in rest)


def _utc_iso(v: datetime) -> str:
    """Naive UTC -> ISO-8601 with 'Z'.

    Without this the browser SILENTLY reads "2026-07-26T19:07:37" as local time and the
    countdown shifts by the zone offset — the number looks right and is wrong. The whole
    project defends itself against false confidence, and a zoneless stamp is its pure form.
    """
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# Use THIS type in every output field carrying a date. A plain `datetime` serializes
# without an offset, and that is a pitfall, not a matter of stylistic preference.
UtcDt = Annotated[datetime, PlainSerializer(_utc_iso, return_type=str)]


def _naive_utc(v: datetime) -> datetime:
    """ISO-8601 from the wire -> naive UTC. The input mirror of `UtcDt`.

    Once the output got a zone (contract v2), the browser started SENDING BACK time with a
    'Z' — `Date.toISOString()`. Pydantic turns that into a datetime WITH A ZONE, while the
    whole backend (columns, `utcnow()`, samples) is naive. Without this conversion /history
    blows up on the subtraction "can't subtract offset-naive and offset-aware datetimes".

    Worse is what cannot be seen: the MySQL driver formats a datetime with `strftime` and
    IGNORES tzinfo, so a parameter with an offset other than UTC (e.g. '+02:00') would land
    in WHERE as wall-clock time and quietly shift the whole range by two hours. With 'Z' the
    same thing comes out by accident — which is why the boundary is closed here instead of
    counting on the client.
    """
    return v if v.tzinfo is None else v.astimezone(timezone.utc).replace(tzinfo=None)


# Use THIS type in every INPUT parameter carrying a date (query, body).
NaiveUtcDt = Annotated[datetime, AfterValidator(_naive_utc)]


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


# --------------------------------------------------------------------------- read
class SeriesStatus(CamelModel):
    series_id: int
    series_key: str
    label: str
    source: str                        # bucket | limit | extra_usage | spend
    sort_order: int
    # The semantics of the series. Without these three fields the UI would have to guess
    # which series is the 5-hour window — from sort_order or from the key prefix, which is
    # exactly what rule 5 of AGENTS.md forbids.
    kind: str | None                   # session | weekly_all | weekly_scoped | ...
    group: str | None                  # session | weekly | ...
    bucket_key: str | None             # five_hour, seven_day, tangelo, ...
    utilization: float | None          # None when there is nothing to show — that is correct
    raw_utilization: float | None      # the last MEASURED value, with no inference
    # The reason this series CANNOT BE measured — verbatim from Anthropic (observed:
    # `org_level_disabled_until`, `org_spend_cap_reached`; the set IS open, so a consumer
    # checks `!== null` and not the content). When it is set, `utilization` is null —
    # otherwise the UI, which computes `utilization ?? rawUtilization`, would draw a
    # measured 0% at the very moment of a hard block. `raw_utilization` is NOT null: it
    # keeps the last MEASURED percent, so the reading survives with its age beside it
    # (`services/status.py:221-269` builds exactly that).
    unavailable_reason: str | None = None
    resets_at: UtcDt | None
    seconds_to_reset: int | None
    # Three different questions about time — confusing them makes a stable reading look
    # like a failure:
    #   captured_at  - when the last SAMPLE was written (dedup skips unchanged values)
    #   confirmed_at - when the value was last CONFIRMED  <- freshness is computed from this
    #   value_since  - since when the value is unchanged  <- "unchanged since ..." uses this
    captured_at: UtcDt | None
    confirmed_at: UtcDt | None
    value_since: UtcDt | None
    freshness: str                     # live | stale | inferred_reset | unknown
    is_active: bool | None
    severity: str | None
    delta_pct_1h: float | None
    # From which SAMPLE delta_pct_1h is counted — the baseline is trimmed to the current
    # window, so the span is sometimes shorter than an hour. Null together with delta_pct_1h.
    delta_from: UtcDt | None
    # The API reports the same limits twice: once as a top-level bucket, once as an entry
    # in limits[]. We detect it from the data (identical utilization + resets_at), with no
    # hardcoded mapping — because hardcoding is exactly what bit us once already.
    # By default the UI is to show only primary=true.
    primary: bool = True
    duplicate_of: str | None = None
    extra: dict[str, Any] | None


class CascadeRung(CamelModel):
    """One rung of the limit cascade: 5 h -> week -> credits -> hard block.

    Amounts IN MINOR UNITS with an exponent, never as a float — and unformatted. The
    backend does not return "38.20 / 90.00 USD"; the UI assembles that, as it is presentation.
    """
    key: str                           # session | weekly | credits | hard_block
    state: str                         # on | off | unknown  ("unknown" != "off")
    # Why the rung is off — the same string from Anthropic as in SeriesStatus.
    # `state` does NOT get a fourth value: withdrawn credits are still `off`, so a consumer
    # that does not know this field will write "disabled" — an incomplete sentence, but
    # a true one.
    reason: str | None = None
    is_current: bool = False           # the rung that limits YOU at this moment
    utilization: float | None = None   # session, weekly
    series_key: str | None = None      # back-reference to the series that gave this value
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


# --------------------------------------------------------------------------- stream
# SSE frame envelopes. `AccountFrame` embeds `AccountStatus` VERBATIM — never a "lite"
# variant. The model is already frozen by /api/status, so reusing it adds not a single
# frozen field; a separate, simplified shape would be a second contract to maintain, and
# THAT is what would break rule 8 — not this envelope.
class HelloFrame(CamelModel):
    contract_version: int
    server_now: UtcDt
    # `subscribed` are the UUIDs found in the database, `unknown` the ones that are not.
    # An unknown UUID is NOT an error (the account may only appear later), but it must be
    # visible: otherwise a typo in a panel's config shows up as silence indistinguishable
    # from idleness — the failure mode this whole project is built to avoid.
    subscribed: list[str]
    unknown: list[str]
    ping_sec: float
    max_lifetime_sec: float


class AccountFrame(CamelModel):
    contract_version: int
    server_now: UtcDt
    account: AccountStatus
    # Warnings for THIS account. The full list in /api/status is computed across accounts
    # and stays there — the poll remains its owner.
    warnings: list[str] = []


class PingFrame(CamelModel):
    server_now: UtcDt


# A Claude Code session that has stopped and is waiting for a human. A MOMENTARY state: it
# lives only in the process memory, it is not in the database and leaves no history.
#
# All the descriptive fields are optional ON PURPOSE. This is the only shape in this
# contract whose source is a Claude Code hook payload, and that changes between client
# versions — `PermissionRequest` has no `tool_use_id` even though `PreToolUse` does. A
# strict model would reject the whole frame here over a missing field that is only
# a caption on a screen anyway.
class SessionAlert(CamelModel):
    key: str
    machine: str | None = None
    reason: str                  # permission | question | plan
    project: str | None = None
    tool: str | None = None
    detail: str | None = None
    since: UtcDt | None = None
    account_uuid: str | None = None
    session_id: str | None = None
    agent_id: str | None = None
    agent_type: str | None = None
    permission_mode: str | None = None


class AlertFrame(CamelModel):
    contract_version: int
    server_now: UtcDt
    # The FULL current set from ALL machines, not a delta. Three independent reasons: the
    # snapshot in stream.py clears the queue, a queue overflow swaps the contents for
    # `lag`, and the project policy (docs/API.md) states outright that every frame is a
    # full state. With a full state, any single frame heals any loss.
    alerts: list[SessionAlert] = []


class SessionAlertResult(CamelModel):
    ok: bool
    machine: str
    accepted: int
    subscribers: int


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
    # client_silent — the client was silent (no batches).
    # no_samples    — the client reported, but not one sample arrived for THIS series.
    # These are two different things and they have to look different on the chart: the
    # second one is a FAILURE, exactly like the `unknown` state in /status.
    kind: str


class HistoryResponse(CamelModel):
    bucket: str
    points: list[HistoryPoint]
    resets: list[UtcDt]
    gaps: list[HistoryGap]
