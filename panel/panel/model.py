"""The contract of /api/status and of the /api/stream frames — the panel's side.

The counterpart of frontend/src/api/types.ts. The contract is FROZEN (rule 8
of AGENTS.md), and the panel is its THIRD consumer, next to /api/status and the UI.

We read DEFENSIVELY: unknown fields are ignored, missing ones yield None. A new
bucket at Anthropic has to work with no change here (rule 5) — and a panel sitting
on a desk must not blow up because the backend grew a field.
"""
CONTRACT_VERSION = 3


def _f(value):
    """A number or None. The contract delivers Decimals as JSON numbers."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class CascadeRung:
    """A rung of the cascade: session -> weekly -> credits -> hard_block.

    `state` has THREE values and "off" is not a synonym for "unknown" — credits
    turned off is information, not knowing about them is the absence of it
    (services/cascade.py).
    """

    __slots__ = ("key", "state", "is_current", "utilization", "series_key",
                 "used_minor", "limit_minor", "currency", "exponent")

    def __init__(self, d):
        d = d or {}
        self.key = d.get("key")
        self.state = d.get("state")
        self.is_current = bool(d.get("isCurrent"))
        self.utilization = _f(d.get("utilization"))
        self.series_key = d.get("seriesKey")
        self.used_minor = d.get("usedMinor")
        self.limit_minor = d.get("limitMinor")
        self.currency = d.get("currency")
        self.exponent = d.get("exponent")


class SeriesStatus:
    """One limit window. `utilization` is in PERCENT 0..100 and is None
    if and only if `freshness == "unknown"`."""

    __slots__ = ("series_id", "series_key", "label", "source", "sort_order",
                 "kind", "group", "bucket_key", "utilization", "raw_utilization",
                 "resets_at", "seconds_to_reset", "captured_at", "confirmed_at",
                 "value_since", "freshness", "is_active", "severity",
                 "delta_pct_1h", "delta_from", "primary", "duplicate_of", "extra")

    def __init__(self, d):
        d = d or {}
        self.series_id = d.get("seriesId")
        self.series_key = d.get("seriesKey")
        self.label = d.get("label")
        self.source = d.get("source")
        self.sort_order = d.get("sortOrder") or 0
        self.kind = d.get("kind")
        self.group = d.get("group")
        self.bucket_key = d.get("bucketKey")
        self.utilization = _f(d.get("utilization"))
        self.raw_utilization = _f(d.get("rawUtilization"))
        self.resets_at = d.get("resetsAt")
        self.seconds_to_reset = d.get("secondsToReset")
        self.captured_at = d.get("capturedAt")
        self.confirmed_at = d.get("confirmedAt")
        self.value_since = d.get("valueSince")
        self.freshness = d.get("freshness") or "unknown"
        self.is_active = d.get("isActive")
        self.severity = d.get("severity")
        self.delta_pct_1h = _f(d.get("deltaPct1h"))
        self.delta_from = d.get("deltaFrom")
        # `primary` defaults to True: were the backend ever to stop sending it,
        # better to show a series twice than not to show it at all.
        self.primary = d.get("primary", True)
        self.duplicate_of = d.get("duplicateOf")
        self.extra = d.get("extra")


class AccountStatus:
    """One account's card — exactly what the `account` frame carries."""

    __slots__ = ("uuid", "label", "email", "display_name", "org_type", "seat_tier",
                 "rate_limit_tier", "subscription_type", "is_enabled",
                 "last_sample_at", "last_batch_at", "last_client_host",
                 "cascade", "series")

    def __init__(self, d):
        d = d or {}
        self.uuid = d.get("uuid")
        self.label = d.get("label")
        self.email = d.get("email")
        self.display_name = d.get("displayName")
        self.org_type = d.get("orgType")
        self.seat_tier = d.get("seatTier")
        self.rate_limit_tier = d.get("rateLimitTier")
        self.subscription_type = d.get("subscriptionType")
        self.is_enabled = d.get("isEnabled", True)
        self.last_sample_at = d.get("lastSampleAt")
        self.last_batch_at = d.get("lastBatchAt")
        self.last_client_host = d.get("lastClientHost")
        self.cascade = [CascadeRung(x) for x in (d.get("cascade") or [])]
        self.series = [SeriesStatus(x) for x in (d.get("series") or [])]

    def rung(self, key):
        for r in self.cascade:
            if r.key == key:
                return r
        return None

    @property
    def title(self):
        """The name in the band header. Configuration can override it — 480 px is tight."""
        return self.email or self.label or self.display_name or (self.uuid or "")[:8]
