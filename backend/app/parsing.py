"""Normalization of the /api/oauth/usage response into a flat list of observations.

PURE FUNCTIONS — zero I/O, zero database dependencies. Every piece of logic that can get
it wrong lives here and is testable against real payloads from backend/tests/fixtures/.

Rules carried over from step 0:
  * The response has 17 top-level keys, 5 of which were new to us. A closed list must not
    be assumed — the parser accepts any new key and reports it as drift.
  * `bucket.utilization` is a float, `limits[].percent` an int, `spend.percent` an int.
    Everything is normalized to 0..100.
  * `resets_at` in the raw response is ISO-8601 with microseconds and an offset. The
    statusline reported an epoch — the parser takes both, because it costs three lines.
  * On a Team account the binding limit is `spend` (the organization's monthly limit), not
    the time windows. That is why `spend` is a first-class series, not a secondary field.
  * Amounts in `spend` are in minor units with an exponent — we do not flatten them to a float.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

# Top-level keys handled separately, not as an ordinary bucket.
_SPECIAL_KEYS = {"limits", "extra_usage", "spend", "member_dashboard_available"}

_SORT = {
    "bucket:five_hour": 10,
    "bucket:seven_day": 20,
    "spend:org": 30,
    "extra:usage": 40,
}

# Labels are UI-VISIBLE CONTENT. An unknown key gets its label from the key itself
# (see `humanize`), so a new bucket at Anthropic shows up on its own, with no entry
# in this table.
_LABELS = {
    "five_hour": "Session (5 h)",
    "seven_day": "Week (all models)",
    "seven_day_opus": "Week — Opus",
    "seven_day_sonnet": "Week — Sonnet",
    "seven_day_omelette": "Week — Design",
    "seven_day_cowork": "Week — Cowork",
    "seven_day_oauth_apps": "Week — OAuth apps",
    "seven_day_overage_included": "Week — with overage",
}

_KIND_LABELS = {
    "session": "Session",
    "weekly_all": "Week (all models)",
    "weekly_scoped": "Week",
}


@dataclass
class Observation:
    series_key: str
    source: str                       # bucket | limit | extra_usage | spend
    display_label: str
    utilization: float | None         # ALWAYS 0..100
    # The reason this series CANNOT BE measured — verbatim from Anthropic. When it is set,
    # `utilization` is None BY DEFINITION: this is not a measured 0%, it is the absence of
    # a meter. See `meter_withdrawn`.
    unavailable_reason: str | None = None
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
    # Whether this series' value comes from a `claude -p "/usage"` dump (and not from the
    # Claude Code cache). It decides the DATE of the measurement: both sources sit in one
    # payload and differ in age. Set from `measurement.fresh_covered`, because only the
    # probe knows what it overwrote with what.
    covered_by_fresh: bool = False


@dataclass
class ParseResult:
    observations: list[Observation]
    seen_keys: set[str]               # every top-level key in the response
    null_keys: set[str]               # keys present but None (the series exists, no value)
    problems: list[str]               # non-fatal: anything we were unable to read


# --------------------------------------------------------------------------- helpers
def parse_pct(v: Any) -> float | None:
    """Tolerates an int, a float and a string with a '%' suffix. The reference
    implementation had to handle all three, so we assume the same."""
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
    """ISO-8601 (the raw response) or an epoch in seconds (the statusline). Returns naive
    UTC TRUNCATED TO WHOLE SECONDS, because the database columns are naive and everything
    is kept in UTC.

    Truncating the microseconds is not cosmetic, and it loses no information. Anthropic stamps
    `resets_at` with the microseconds of ITS OWN RESPONSE, not of the window boundary — in
    one response `five_hour` ends at `00:59:59.056340` and `seven_day` at `15:59:59.056361`
    (21 us apart, because the fields are computed one after another). Measured effect: 63
    samples over 6 h gave 63 different `resets_at`; truncated to the second, 2 values remain.

    Because of that the comparison `last_resets_at == o.resets_at` was practically always
    false, which silently disabled THREE mechanisms at once: dedup (every sample written as
    a change), the monotonicity guard (it fired only when resets_at was unchanged) and the
    detection of reset boundaries in history (61 "resets" a day instead of five).
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        # >1e12 => milliseconds; the endpoint does not do this, but the guard is cheap
        secs = float(v) / 1000.0 if float(v) > 1e12 else float(v)
        dt = datetime.fromtimestamp(secs, tz=timezone.utc).replace(tzinfo=None)
        return dt.replace(microsecond=0)
    if isinstance(v, str):
        s = v.strip().replace("Z", "+00:00")
        # fromisoformat in 3.12 handles microseconds and an offset, but not >6 digits
        m = re.match(r"^(.*\.\d{1,6})\d*(.*)$", s)
        if m:
            s = m.group(1) + m.group(2)
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt.replace(microsecond=0)
    return None


def same_reset_window(a: datetime | None, b: datetime | None, eps_sec: float) -> bool:
    """Whether two `resets_at` describe THE SAME window.

    Equality cannot settle it, because the window boundary Anthropic reports WOBBLES.
    Measured: 49 samples over the course of 3 hours, one session window, values from
    `00:59:59.014384` to `01:00:00.982268` — a spread of just under 2 seconds, crossing
    a minute boundary along the way (so neither truncation to the second nor to the minute
    settles it; truncation stays, but only to keep the noise out of the database).

    A tolerance settles it unambiguously: a real reset moves the boundary by a WHOLE WINDOW
    — 5 hours for the session, 7 days for the week. A threshold of a few minutes is two
    orders of magnitude larger than the wobble and two orders smaller than the shortest window.

    A missing value on both sides counts as the same window (e.g. `spend` has no reset);
    missing on one side is a change.

    ANSWERS THE DEDUP QUESTION ("has anything changed"), not the monotonicity guard's. The
    guard asks for PROOF and has `known_same_reset_window` for that — one bool for both
    questions froze the current state.
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs((a - b).total_seconds()) <= eps_sec


def known_same_reset_window(a: datetime | None, b: datetime | None, eps_sec: float) -> bool:
    """Whether it is KNOWN to be the same window — as opposed to `same_reset_window`, which
    answers "yes" when both boundaries are missing.

    The two questions look like one, and that is why a single bool served both for a long
    time. The difference, however, is fundamental:

      * DEDUP asks "has anything changed". Twice no boundary is no change, so
        `same_reset_window(None, None) is True` is CORRECT there — and the dedup of the
        `spend:org` and `extra:usage` series, which NEVER have a boundary, rests on it.
      * THE MONOTONICITY GUARD asks "do I have PROOF that the window has not rolled over".
        Twice no boundary is no proof, not proof of the opposite.

    Ignorance taken for proof froze the current state. Right after a reset Anthropic reports
    `resets_at: null` for the fresh window at 0% usage, so a drop of 92% -> 0% with two
    NULLs looked like a stale reading from another machine: the sample went to the database
    with the `stale_read` flag while `series_state` stood still. Live deployment 2026-07-30:
    five consecutive samples 11:51:05-11:55:13 UTC, the state frozen at 92% against a real
    usage of 0-2%, released only by a new boundary after 9.5 minutes. For `spend:org` and
    `extra:usage` this was PERMANENT — a boundary will never arrive there, so nothing would
    have released it.

    The asymmetry of the costs is unambiguous, but not as cheap as it looks. Accepting a
    stale reading corrupts the state until the NEXT measurement of that series — usually a
    minute — except that the upper bound is set not by the probe's frequency but by its
    silence: when the client goes quiet right after the write, the wrong value holds until
    `freshness()` degrades it, that is until `CLIENT_SILENT_SEC` (6 h by default,
    `app/config.py:38`), and for that whole time it is served as `live`/`stale` — as
    a value and not as ignorance. A frozen state lies for exactly as long, and on top of
    that has no way out of it: for `spend:org` and `extra:usage` a boundary will NEVER
    arrive. The sample reaches the database in both variants, so neither loses history. The
    guard therefore gets only proof, never a guess.
    """
    return a is not None and b is not None and same_reset_window(a, b, eps_sec)


def carry_reset_window(prev: datetime | None, incoming: datetime | None,
                       at: datetime) -> datetime | None:
    """Which window boundary the STATE is to hold when the measurement brought none.

    A measurement without a boundary does not mean "there is no boundary", only "this
    reading does not know it". There are two reasons and both are normal: Anthropic reports
    no boundary for a window at 0% usage, and the probe zeroes a boundary that has gone
    stale in its OWN cache (the `reset-in-progress` rule, `client/usage-probe.py`).

    Both extreme solutions are wrong:
      * overwrite with NULL — we lose a boundary that STILL describes the running window.
        The countdown in the UI lives off it, along with `seconds_to_reset`, the trimming of
        the delta baseline to the window and the `inferred_reset` inference during a longer
        client silence (`app/freshness.py`).
      * always hold the old one — the state then claims that the current window ends at an
        instant that HAS ALREADY PASSED. A confident-looking untruth. Rule 4 of AGENTS.md
        is literally about `utilization`, not about time fields, so this is not a
        quotation of it but an analogy: the same move, namely swapping ignorance for a
        definite number the recipient has no way to challenge.

    The clock settles it, not a guess. A boundary in the future describes a window that has
    not ended yet — a measurement taken before it therefore belongs to that same window and
    the boundary remains true. A boundary that has passed says NOTHING about the current
    window; the state then admits its ignorance (NULL) instead of reporting a stale number.

    The comparison goes against `at`, that is against the time of the MEASUREMENT and not
    against "now": a sample from the backlog is judged relative to the instant at which it
    was measured. Without a tolerance, exactly like `freshness()` and `_delta_1h` — the
    tolerance from rule 9 concerns comparing TWO boundaries with each other, not a boundary
    with a point in time.
    """
    if incoming is not None:
        return incoming
    if prev is not None and prev > at:
        return prev
    return None


def meter_withdrawn(block: Any) -> str | None:
    """The reason this meter DOES NOT WORK — or None, when it does.

    When an organization exhausts its global spend ceiling, Anthropic does not stop
    answering — it ZEROES THE METER: `percent` drops from 91 to 0, `used` from 273.15 EUR
    to 0.00, `limit` and `cap` disappear (null), `severity` goes back from `critical` to
    `normal`. Taken at face value, that zero is a confident, measured "you have the whole
    limit" at the very moment of a hard block.

    The payload of this state is structurally IDENTICAL to the payload of a Max account that
    never turned credits on: `enabled:false`, `cap:null`, `percent:0`, `used:0` in both. They
    are told apart SOLELY by `disabled_reason` — the only discriminator we get.

    We do not interpret the CONTENT of the reason (rule 5): every non-empty string means
    "withdrawn", and the string itself travels verbatim, all the way to the UI. The set IS
    open — on a single account two different strings were observed within one day
    (`org_level_disabled_until`, `org_spend_cap_reached`).

    Exhausting YOUR OWN pool is NOT a withdrawal: `enabled` then stays `true`,
    `disabled_reason` is `null`, and `percent` reaches 100 (measured: used 300.04 against a
    limit of 300.00 EUR) — the meter works and tells the truth. That is why the verdict rests
    on the `enabled` flag, never on how high the percentage is: treating 100% as a withdrawal
    would take away the only correct number at exactly the moment it is needed most.

    It reads the same field names in the `spend` block (`enabled`) and in `extra_usage`
    (`is_enabled`), so it works the same on the raw response as on `extra` stored in the
    database — which means state from before this change reads correctly without a migration.
    """
    if not isinstance(block, dict):
        return None
    reason = block.get("disabled_reason")
    if not isinstance(reason, str) or not reason.strip():
        return None
    enabled = block.get("enabled")
    if not isinstance(enabled, bool):
        enabled = block.get("is_enabled")
    if enabled is True:
        # There is a reason, but the gate is open — the meter works and its number holds.
        return None
    return reason


# (captured_at, utilization, resets_at) — one row of a series' run.
Sample = tuple[datetime, float | None, datetime | None]


def window_start_index(rows: Sequence[Sample], eps_sec: float, monotonic_eps: float) -> int:
    """Index of the first sample from the CURRENT window. `rows` ascending, no `stale_read`.

    Three signals, because `resets_at` happens to be None for two different reasons and no
    single one of them sees every reset:
      shift  - the boundary jumped by a whole window (with a tolerance, rule 9),
      passed - the sample is younger than the known boundary; the only signal for the
               probe's `reset-in-progress`, where sanitize() zeroes `resets_at` while usage
               may be rising,
      drop   - utilization fell; impossible within a window (the monotonicity guard). The
               rescue for a series with no known boundary — at 0% Anthropic reports no
               `resets_at`.

    A false signal SHORTENS the window, it never pulls in a sample from the previous one.
    """
    if not rows:
        return 0
    start = 0
    cur = rows[0][2]          # the boundary of the window we are walking
    prev_u = rows[0][1]
    for i in range(1, len(rows)):
        t, u, r = rows[i]
        drop = prev_u is not None and u is not None and u < prev_u - monotonic_eps
        shift = (cur is not None and r is not None
                 and not same_reset_window(cur, r, eps_sec))
        passed = cur is not None and (t - cur).total_seconds() > eps_sec
        if drop or shift or passed:
            start, cur = i, r
        elif cur is None and r is not None:
            cur = r           # the boundary came back after a gap — NOT a second reset
        if u is not None:
            prev_u = u        # a null does not erase the reference point for a drop
    return start


def _slug(s: Any) -> str:
    if s is None:
        return "-"
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_") or "-"


def humanize(key: str) -> str:
    return _LABELS.get(key) or key.replace("_", " ").capitalize()


def limit_series_key(kind, group, model, surface) -> str:
    key = "limit:%s|%s|%s|%s" % (_slug(kind), _slug(group), _slug(model), _slug(surface))
    if len(key) > 255:                       # never in practice; the parser must not throw
        import hashlib
        key = key[:240] + ":h" + hashlib.sha256(key.encode()).hexdigest()[:8]
    return key


def probe_key(o: Observation) -> str | None:
    """The series identifier IN THE PROBE'S LANGUAGE — to be compared with
    `measurement.fresh_covered`.

    This is NOT a `series_series_key`: the probe knows neither `group` nor `surface` nor the
    slugging, and it builds the key out of what it has at hand in `usage` (see `_limit_key`
    in client/usage-probe.py). A divergence here is SILENT — the sets would never match,
    `covered_by_fresh` would never be set and the timestamping would quietly fall back to the
    state from before this change. Hence: NO `_slug` (the probe sends a raw `display_name`)
    and NO `surface` (`merge` matches on `kind`+`model`, so two limits differing only in
    surface really are both covered).

    `spend` and `extra_usage` return None: the `/usage` dump NEVER contains them, so the
    question of coverage makes no sense for them."""
    if o.source == "bucket":
        return "bucket:%s" % o.bucket_key
    if o.source == "limit":
        return "limit:%s:%s" % (o.kind or "?", o.model_display_name or "-")
    return None


# --------------------------------------------------------------------------- main
def parse_usage(payload: Any, fresh_covered: frozenset[str] = frozenset()) -> ParseResult:
    """Turns the /api/oauth/usage response into a list of observations.

    Never throws. Whatever it cannot read it reports in `problems`, and it processes the
    rest — better to record 15 of 17 series than to reject the whole measurement.

    `fresh_covered` is the set of identifiers from the probe's `measurement.fresh_covered`.
    An empty one (the default) means "nothing comes from the dump" — and that is a correct,
    graceful degradation for a payload without that field, not a compatibility branch.
    """
    obs: list[Observation] = []
    problems: list[str] = []
    seen: set[str] = set()
    nulls: set[str] = set()

    if not isinstance(payload, dict):
        return ParseResult([], seen, nulls, ["payload is not an object"])

    for key, val in payload.items():
        seen.add(key)
        if val is None:
            nulls.add(key)

    # --- 1. top-level buckets ---------------------------------------------
    for key, val in payload.items():
        if key in _SPECIAL_KEYS:
            continue
        if val is None:
            continue
        if not isinstance(val, dict):
            # e.g. member_dashboard_available=bool lands here only if the schema changes
            problems.append("key %s has an unexpected type %s" % (key, type(val).__name__))
            continue
        if "utilization" not in val:
            problems.append("bucket %s without a utilization field" % key)
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
        problems.append("limits is not a list")
        limits = None
    for i, lim in enumerate(limits or []):
        if not isinstance(lim, dict):
            problems.append("limits[%d] is not an object" % i)
            continue
        scope = lim.get("scope") or {}
        model = ((scope.get("model") or {}).get("display_name")
                 if isinstance(scope, dict) else None)
        surface = ((scope.get("surface") or {}).get("display_name")
                   if isinstance(scope, dict) else None)
        kind, group = lim.get("kind"), lim.get("group")
        label = _KIND_LABELS.get(kind or "", (kind or "limit").replace("_", " "))
        # Em dash, not a hyphen — the label is UI-visible content ("Week — Fable").
        if model:
            label = "%s — %s" % (label, model)
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
        eu_reason = meter_withdrawn(eu)
        obs.append(Observation(
            series_key="extra:usage", source="extra_usage",
            display_label="Extra credits",
            # A withdrawn meter has no measurement. The amounts and flags stay untouched in
            # `extra`, the raw response lies in `raw_payloads` — nothing is lost but the
            # phantom zero.
            utilization=None if eu_reason else parse_pct(eu.get("utilization")),
            unavailable_reason=eu_reason,
            sort_order=_SORT["extra:usage"],
            extra={k: v for k, v in eu.items() if k != "utilization"},
        ))
    elif eu is not None:
        problems.append("extra_usage is not an object")

    # --- 4. spend — on Team this IS the binding limit ----------------------
    sp = payload.get("spend")
    if isinstance(sp, dict):
        sp_reason = meter_withdrawn(sp)
        obs.append(Observation(
            series_key="spend:org", source="spend",
            # NOT "the organization limit": this is the pool ALLOCATED TO YOU (300.00 EUR a
            # month). The organization's own ceiling is something else and does not exist in
            # the contract — when it runs out this series does not grow, it goes dark (see
            # meter_withdrawn). We change ONLY the label; `series_key` is identity and stays.
            display_label="Spend limit (your pool)",
            utilization=None if sp_reason else parse_pct(sp.get("percent")),
            unavailable_reason=sp_reason,
            severity=sp.get("severity"),
            sort_order=_SORT["spend:org"],
            # Amounts stay in minor units with an exponent — no flattening.
            extra={k: v for k, v in sp.items() if k not in ("percent", "severity")},
        ))
    elif sp is not None:
        problems.append("spend is not an object")

    if fresh_covered:
        for o in obs:
            k = probe_key(o)
            if k is not None and k in fresh_covered:
                o.covered_by_fresh = True

    return ParseResult(obs, seen, nulls, problems)
