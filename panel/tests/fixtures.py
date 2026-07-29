"""Sceny testowe w ksztalcie kontraktu.

Dane demonstracyjne sa te same co w makiecie (blok text/x-dc na koncu
"Ekran 3.5 cala.dc.html"), zeby render dalo sie porownac z projektem 1:1.
Stany, ktorych w produkcji nie da sie wywolac, sa tu tak samo jak
w frontend/src/mocks — bo inaczej nikt ich nigdy nie zobaczy przed awaria.
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


# --- sceny ------------------------------------------------------------------


def base():
    """Dokladnie dane z makiety: konto Max bez kredytow i konto Team z kredytami."""
    a = account(
        "00000000-0000-4000-8000-000000000003", "you@example.org",
        cascade=[rung("session", "on", isCurrent=True, utilization=31),
                 rung("weekly", "on", utilization=30),
                 rung("credits", "off"),
                 rung("hard_block", "unknown")],
        series=[
            series("limit:session|session|-|-", "Sesja", kind="session",
                   bucketKey="five_hour", utilization=31, rawUtilization=31,
                   resetsAt=_at(52), isActive=True, severity="normal",
                   confirmedAt=_at(-0.32)),
            series("bucket:seven_day", "Tydzień (wszystkie modele)",
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
            series("limit:session|session|-|-", "Sesja", kind="session",
                   bucketKey="five_hour", utilization=12, rawUtilization=12,
                   resetsAt=_at(112), confirmedAt=_at(-0.93)),
            series("bucket:seven_day", "Tydzień (wszystkie modele)",
                   kind="weekly_all", bucketKey="seven_day", sort_order=20,
                   utilization=100, rawUtilization=100, resetsAt=_at(3712),
                   isActive=True, severity="critical"),
        ])
    return [a, b]


def states():
    """Stany, ktorych w produkcji nie da sie wywolac na zadanie:
    `unknown` (awaria klienta) i `inferred_reset` (okno wrocilo w ciszy)."""
    a = account(
        "aaaa1111-0000-0000-0000-000000000001", "unknown@example.pl",
        cascade=[rung("credits", "unknown")],
        series=[
            series("limit:session|session|-|-", "Sesja", kind="session",
                   bucketKey="five_hour", freshness="unknown", utilization=None,
                   rawUtilization=42, resetsAt=_at(31), confirmedAt=_at(-380)),
            series("bucket:seven_day", "Tydzień", kind="weekly_all",
                   bucketKey="seven_day", sort_order=20, freshness="unknown",
                   utilization=None, rawUtilization=88, resetsAt=_at(4000)),
        ])
    b = account(
        "bbbb2222-0000-0000-0000-000000000002", "reset@example.pl",
        tier="default_claude_max_20x",
        cascade=[rung("credits", "on", usedMinor=125, limitMinor=30000,
                      currency="USD", exponent=2)],
        series=[
            series("limit:session|session|-|-", "Sesja", kind="session",
                   bucketKey="five_hour", freshness="inferred_reset",
                   utilization=0, rawUtilization=97, resetsAt=None),
            series("bucket:seven_day", "Tydzień", kind="weekly_all",
                   bucketKey="seven_day", sort_order=20, utilization=7,
                   rawUtilization=7, resetsAt=_at(9000)),
        ])
    return [a, b]


def edges():
    """Skrajnosci ukladu: dluga nazwa, trzycyfrowa sesja, brak drugiego konta."""
    a = account(
        "cccc3333-0000-0000-0000-000000000003",
        "bardzo.dluga.nazwa.konta.ktora.nie.miesci.sie@przyklad.example.pl",
        cascade=[rung("credits", "on", isCurrent=True, usedMinor=123456,
                      limitMinor=200000, currency="USD", exponent=2)],
        series=[
            series("limit:session|session|-|-", "Sesja", kind="session",
                   bucketKey="five_hour", utilization=100, rawUtilization=100,
                   resetsAt=_at(0.2)),
            series("bucket:seven_day", "Tydzień", kind="weekly_all",
                   bucketKey="seven_day", sort_order=20, utilization=100,
                   rawUtilization=100, resetsAt=_at(2880)),
        ])
    return [a, None]


SCENES = {"base": base, "states": states, "edges": edges}
