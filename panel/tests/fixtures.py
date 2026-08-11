"""Test scenes shaped like the contract.

The demonstration data is the same as in the 3.5-inch screen mockup (the text/x-dc
block at the end of it; the mockup is not tracked here), so the render can be
compared with the design 1:1.
States that a live run cannot be made to produce are here just as they are
in frontend/src/mocks — because otherwise nobody would ever see them before a failure.
"""
from datetime import timedelta

from panel import fmt, model

NOW_ISO = "2026-07-26T19:07:40Z"


def _at(offset_min, base=NOW_ISO):
    d = fmt.parse_utc(base) + timedelta(minutes=offset_min)
    return d.isoformat().replace("+00:00", "Z")


def series(key, label, **kw):
    d = {
        "seriesId": abs(hash(key)) % 1000,
        "seriesKey": key,
        "label": label,
        "source": kw.pop("source", "limit"),
        "sortOrder": kw.pop("sort_order", 15),
        "primary": True,
        "freshness": "live",
        "utilization": None,
        "rawUtilization": None,
        "resetsAt": None,
        "confirmedAt": _at(-1),
        "capturedAt": _at(-1),
    }
    d.update({k: v for k, v in kw.items()})
    return d


def account(uuid, email, **kw):
    d = {
        "uuid": uuid,
        "email": email,
        "orgType": kw.pop("org_type", "claude_max"),
        "rateLimitTier": kw.pop("tier", "default_claude_max_5x"),
        "subscriptionType": kw.pop("subscription", "max"),
        "lastSampleAt": kw.pop("last_sample", _at(-1)),
        "lastBatchAt": _at(-1),
        "lastClientHost": "desktop",
        "cascade": kw.pop("cascade", []),
        "series": kw.pop("series", []),
    }
    d.update(kw)
    return model.AccountStatus(d)


def rung(key, state, **kw):
    d = {"key": key, "state": state}
    d.update(kw)
    return d


# --- scenes -----------------------------------------------------------------


def base():
    """Exactly the data from the mockup: a Max account with no credits and a Team one with them."""
    a = account(
        "00000000-0000-4000-8000-000000000003", "you@example.org",
        cascade=[rung("session", "on", isCurrent=True, utilization=31),
                 rung("weekly", "on", utilization=30),
                 rung("credits", "off"),
                 rung("hard_block", "unknown")],
        series=[
            series("limit:session|session|-|-", "Session", kind="session",
                   bucketKey="five_hour", utilization=31, rawUtilization=31,
                   resetsAt=_at(52), isActive=True, severity="normal",
                   confirmedAt=_at(-0.32)),
            series("bucket:seven_day", "Week (all models)",
                   kind="weekly_all", bucketKey="seven_day", sort_order=20,
                   utilization=30, rawUtilization=30, resetsAt=_at(8213)),
        ])
    b = account(
        "00000000-0000-4000-8000-000000000005", "billing@example.org",
        org_type="claude_team", tier="default_claude_team_standard",
        subscription="team", last_sample=_at(-0.93),
        cascade=[rung("session", "on", utilization=12),
                 rung("weekly", "on", utilization=100),
                 rung("credits", "on", isCurrent=True, utilization=42,
                      usedMinor=3820, limitMinor=9000, currency="USD", exponent=2),
                 rung("hard_block", "on", limitMinor=9000, currency="USD",
                      exponent=2)],
        series=[
            series("limit:session|session|-|-", "Session", kind="session",
                   bucketKey="five_hour", utilization=12, rawUtilization=12,
                   resetsAt=_at(112), confirmedAt=_at(-0.93)),
            series("bucket:seven_day", "Week (all models)",
                   kind="weekly_all", bucketKey="seven_day", sort_order=20,
                   utilization=100, rawUtilization=100, resetsAt=_at(3712),
                   isActive=True, severity="critical"),
        ])
    return [a, b]


def states():
    """States that a live run cannot be made to produce on demand:
    `unknown` (a client failure) and `inferred_reset` (the window came back in silence)."""
    a = account(
        "aaaa1111-0000-0000-0000-000000000001", "unknown@example.org",
        cascade=[rung("credits", "unknown")],
        series=[
            series("limit:session|session|-|-", "Session", kind="session",
                   bucketKey="five_hour", freshness="unknown", utilization=None,
                   rawUtilization=42, resetsAt=_at(31), confirmedAt=_at(-380)),
            series("bucket:seven_day", "Week", kind="weekly_all",
                   bucketKey="seven_day", sort_order=20, freshness="unknown",
                   utilization=None, rawUtilization=88, resetsAt=_at(4000)),
        ])
    b = account(
        "bbbb2222-0000-0000-0000-000000000002", "reset@example.org",
        tier="default_claude_max_20x",
        cascade=[rung("credits", "on", usedMinor=125, limitMinor=30000,
                      currency="USD", exponent=2)],
        series=[
            series("limit:session|session|-|-", "Session", kind="session",
                   bucketKey="five_hour", freshness="inferred_reset",
                   utilization=0, rawUtilization=97, resetsAt=None),
            series("bucket:seven_day", "Week", kind="weekly_all",
                   bucketKey="seven_day", sort_order=20, utilization=7,
                   rawUtilization=7, resetsAt=_at(9000)),
        ])
    return [a, b]


def edges():
    """Layout extremes: a long name, a three-digit session, no second account."""
    a = account(
        "cccc3333-0000-0000-0000-000000000003",
        "very.long.account.name.that.does.not.fit.here@example.example.org",
        cascade=[rung("credits", "on", isCurrent=True, usedMinor=123456,
                      limitMinor=200000, currency="USD", exponent=2)],
        series=[
            series("limit:session|session|-|-", "Session", kind="session",
                   bucketKey="five_hour", utilization=100, rawUtilization=100,
                   resetsAt=_at(0.2)),
            series("bucket:seven_day", "Week", kind="weekly_all",
                   bucketKey="seven_day", sort_order=20, utilization=100,
                   rawUtilization=100, resetsAt=_at(2880)),
        ])
    return [a, None]


SCENES = {"base": base, "states": states, "edges": edges}
