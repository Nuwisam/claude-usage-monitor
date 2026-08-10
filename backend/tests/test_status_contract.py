"""The /api/status v3 contract — what the UI stands on.

It guards three things whose breakage is invisible in the backend code and shows up only
on the screen:
  1. every timestamp on the wire carries a zone (without it the browser SILENTLY reads UTC
     as local time and the countdown is shifted),
  2. series carry their own semantics (`kind`/`group`/`bucketKey`), so the UI need not
     guess which series is the 5 h window,
  3. `unknown` never turns into a zero.
"""
import json
import re
from datetime import timedelta

from sqlalchemy import select
from tests.test_ingest_e2e import (  # noqa: F401 — the `db` fixture comes along with these
    ACCOUNT_MAX, ACCOUNT_TEAM, REAL, db, ingest_one, payload, utcnow, with_util,
)

from app.models import Account
from app.services.status import CONTRACT_VERSION, build_status

# ISO-8601 with an offset: "2026-07-26T19:07:37.564772Z" or "...+00:00"
ISO_TZ = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
DATE_KEYS = ("At", "Now", "resetsAt", "From")


def walk_timestamps(node, path=""):
    """Every field whose name looks like a date, plus the path for the error message."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and (k.endswith(DATE_KEYS) or k in ("from", "to", "t")):
                yield "%s.%s" % (path, k), v
            else:
                yield from walk_timestamps(v, "%s.%s" % (path, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_timestamps(v, "%s[%d]" % (path, i))


async def wire(db):
    """The response as the browser will see it: camelCase aliases, JSON."""
    st = await build_status(db)
    return json.loads(st.model_dump_json(by_alias=True))


async def test_every_timestamp_has_a_zone(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    body = await wire(db)

    found = list(walk_timestamps(body))
    assert found, "response contains no timestamp at all — the test would be empty"
    for path, value in found:
        assert ISO_TZ.match(value), "%s = %r has no zone" % (path, value)


async def test_contract_version_is_the_declared_version(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    assert (await wire(db))["contractVersion"] == CONTRACT_VERSION == 3


async def test_series_carry_semantics_not_just_a_key(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    series = (await wire(db))["accounts"][0]["series"]

    for s in series:
        assert {"kind", "group", "bucketKey"} <= set(s)
    # the 5 h window can be picked out without reading the key string or sort_order
    sessions = [s for s in series if s["kind"] == "session" or s["bucketKey"] == "five_hour"]
    assert sessions


async def test_cascade_is_on_the_account_and_has_four_tiers(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    cascade = (await wire(db))["accounts"][0]["cascade"]

    assert [r["key"] for r in cascade] == ["session", "weekly", "credits", "hard_block"]
    assert sum(1 for r in cascade if r["isCurrent"]) == 1


async def test_exactly_one_function_builds_the_account_card(db, monkeypatch):
    """Proof that `/api/status` and the SSE publisher have no way of drifting apart.

    The account card is built in `build_account_status`; `build_status` is only a loop
    over accounts. If anyone ever duplicated the card assembly for the stream — and that
    is a natural temptation, since the publisher already knows the account and "it is
    just a copy" — this test would show the drift at once.

    `utcnow` is pinned because three fields depend on the moment of reading (`freshness`,
    `secondsToReset`, `deltaPct1h`) and without it the comparison would catch a difference
    of a second instead of a difference of logic.
    """
    import app.services.status as stat

    await ingest_one(db, machine_name="desktop", payload=payload(account=ACCOUNT_MAX))
    await db.commit()
    await ingest_one(db, machine_name="laptop", payload=payload(account=ACCOUNT_TEAM))
    await db.commit()

    now = utcnow()
    monkeypatch.setattr(stat, "utcnow", lambda: now)

    aggregate = json.loads((await build_status(db)).model_dump_json(by_alias=True))
    assert len(aggregate["accounts"]) == 2, "scenario should have produced two accounts"

    for card in aggregate["accounts"]:
        acc = (await db.execute(
            select(Account).where(Account.account_uuid == card["uuid"])
        )).scalar_one()
        individual, warnings = await stat.build_account_status(
            db, acc, now=now, last_batch_at=await stat.last_batch_time(db, acc.id),
        )
        assert json.loads(individual.model_dump_json(by_alias=True)) == card, \
            "account card %s differs between paths" % card["uuid"]
        assert set(warnings) <= set(aggregate["warnings"])


async def test_last_batch_time_agrees_with_the_grouped_variant(db):
    """The publisher computes `last_batch_at` with a different query than `/api/status`
    (one account instead of GROUP BY). The only fact with two sources — so it gets a test."""
    await ingest_one(db, machine_name="desktop", payload=payload(account=ACCOUNT_MAX))
    await ingest_one(db, machine_name="laptop", payload=payload(account=ACCOUNT_TEAM))
    await db.commit()

    import app.services.status as stat
    grouped = await stat._last_batch_times(db)
    for acc in (await db.execute(select(Account))).scalars().all():
        assert await stat.last_batch_time(db, acc.id) == grouped.get(acc.id)


async def test_unknown_has_no_number_but_keeps_the_last_measurement(db, monkeypatch):
    """The same condition that test_a_live_client_with_no_samples_gives_unknown guards —
    checked here ON THE WIRE, because it is the UI that gets `null` and must really get it.

    The scenario: a measurement of 42%, the window resets an hour later, and after the
    reset batches arrive WITHOUT a single sample. The client is alive, so this is not
    idleness — it is a failure, and the only correct answer is "unknown", never "0%".
    """
    import app.services.ingest as ing
    import app.services.status as stat

    t0 = utcnow()
    u = with_util(five_hour=42.0)
    u["five_hour"]["resets_at"] = (t0 + timedelta(hours=1)).isoformat() + "Z"
    await ingest_one(db, machine_name="desktop", payload=payload(usage=u))

    # a batch AFTER the window reset, but with no series at all in the payload
    monkeypatch.setattr(ing, "utcnow", lambda: t0 + timedelta(hours=2))
    await ingest_one(db, machine_name="desktop", payload=payload(usage={}))
    await db.commit()

    monkeypatch.setattr(stat, "utcnow", lambda: t0 + timedelta(hours=2, minutes=5))
    series = (await wire(db))["accounts"][0]["series"]

    unknowns = [s for s in series if s["freshness"] == "unknown"]
    assert unknowns, "scenario did not produce an unknown state — the test would be empty"
    for s in unknowns:
        assert s["utilization"] is None, "unknown rendered as a number"
        assert s["rawUtilization"] is not None, "lost the last measurement"


# ------------------------------------------------- withdrawn meter on the wire
async def test_withdrawn_meter_leaves_the_last_measured_percent_and_amounts(db):
    """The phantom is the `percent: 0` FROM THE WITHDRAWAL PAYLOAD, not the value from
    before the block. The latter is a measurement and stays on screen — otherwise closing
    the gate ERASES the only usage figure there is, worsening the view instead of fixing it.

    `utilization` (the CURRENT number) disappears, because there is no current one.
    `rawUtilization` (the last MEASURED) stays — exactly as with `unknown`, rule 4."""
    from tests.team import USAGE_ACTIVE, USAGE_WITHDRAWN, team_payload

    now = (utcnow() - timedelta(seconds=120)).replace(microsecond=0)
    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_ACTIVE, captured_at=now))
    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_WITHDRAWN,
                                          captured_at=now + timedelta(seconds=60)))
    await db.commit()

    body = await wire(db)
    spend = {s["seriesKey"]: s for s in body["accounts"][0]["series"]}["spend:org"]
    assert spend["utilization"] is None, "there is no current number and it must not be faked"
    assert spend["rawUtilization"] == 93.0, "last MEASURED percent stays"
    assert spend["unavailableReason"] == "org_level_disabled_until"

    # The amounts also come from the last measurement — the withdrawal payload has `used`
    # zeroed and `limit: null`, so taken from it they would show 0.00 EUR as a fact.
    assert spend["extra"]["used"]["amount_minor"] == 27795
    assert spend["extra"]["limit"]["amount_minor"] == 30000

    # The stamps describe THAT number, not the moment its absence was established.
    assert spend["confirmedAt"].startswith(now.isoformat()[:19])

    credits = {r["key"]: r for r in body["accounts"][0]["cascade"]}["credits"]
    assert credits["state"] == "off" and credits["reason"] == "org_level_disabled_until"
    assert (credits["usedMinor"], credits["limitMinor"]) == (27795, 30000)


async def test_a_series_with_a_reason_has_no_current_number(db):
    """An implication over the WHOLE response, not one series: a reason rules out the
    CURRENT number. `utilization` beside a reason would assert that precisely what cannot
    be measured was measured. `rawUtilization` does not — a measurement from before the
    block."""
    from tests.team import USAGE_WITHDRAWN, team_payload

    await ingest_one(db, machine_name="desktop", payload=payload())           # Max
    await ingest_one(db, machine_name="laptop", payload=team_payload(USAGE_WITHDRAWN))
    await db.commit()

    body = await wire(db)
    all_series = [s for a in body["accounts"] for s in a["series"]]
    assert all_series, "no series — the test would be empty"
    for s in all_series:
        if s["unavailableReason"] is not None:
            assert s["utilization"] is None, s["seriesKey"]

    for a in body["accounts"]:
        for r in a["cascade"]:
            if r["reason"] is not None:
                assert r["state"] != "on" or r["key"] == "hard_block"


async def test_contract_version_stays_at_three_despite_new_fields(db):
    """Adding a field does not break compatibility — the `deltaFrom` precedent. A consumer
    that does not know `unavailableReason` gets exactly what it used to get."""
    from tests.team import USAGE_WITHDRAWN, team_payload

    await ingest_one(db, machine_name="desktop", payload=team_payload(USAGE_WITHDRAWN))
    await db.commit()
    body = await wire(db)

    assert body["contractVersion"] == CONTRACT_VERSION == 3
    assert all("unavailableReason" in s
               for a in body["accounts"] for s in a["series"])
    assert all("reason" in r for a in body["accounts"] for r in a["cascade"])


async def test_a_phantom_zero_written_before_this_change_does_not_return_to_the_screen(db):
    """State written by the PREVIOUS backend version holds a phantom zero in `series_state`:
    the parser recorded `percent: 0` as a measurement back then. After the rollout that row
    still lies there — as long as the meter is withdrawn, no new value will arrive to
    overwrite it. The view must reject it by itself, on the basis of `last_extra`."""
    from sqlalchemy import update as sa_update

    from app.models import SeriesState, UsageSeries
    from tests.team import USAGE_WITHDRAWN, team_payload

    await ingest_one(db, machine_name="desktop", payload=team_payload(USAGE_WITHDRAWN))
    await db.commit()
    series = (await db.execute(
        select(UsageSeries).where(UsageSeries.series_key == "spend:org")
    )).scalar_one()
    # Exactly what the previous version left: zero as a measurement, with a reason in extra.
    await db.execute(sa_update(SeriesState)
                     .where(SeriesState.series_id == series.id)
                     .values(last_utilization=0))
    await db.execute(sa_update(UsageSeries)
                     .where(UsageSeries.id == series.id).values(ever_non_null=True))
    await db.commit()

    spend = {s["seriesKey"]: s
             for s in (await wire(db))["accounts"][0]["series"]}["spend:org"]
    assert spend["utilization"] is None
    assert spend["rawUtilization"] is None, "a phantom zero from the database must not return on the wire"
    assert spend["unavailableReason"] == "org_level_disabled_until"
