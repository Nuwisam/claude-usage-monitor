"""A window the account does not have — Ultra, 2026-10-31.

Anthropic removed the 5-hour limit from Ultra (formerly Max 20x). The raw response since then
says `"five_hour": null` and `limits[]` carries no `kind=session` entry
(`fixtures/usage_ultra_no_session.json`, the `usage_raw` of a real batch). In ~16,000 earlier
samples `five_hour` was never null: an idle window is a dict `{utilization: 0, resets_at: null}`.
So a null there is information, not a failed measurement — and before this change the series
froze at its last sample, the cards showed a day-old percentage and the cascade marked that
frozen rung as current.

The owner moves between Pro, Max and Ultra several times a month, so the state must follow
the payload both ways, any number of times, decided by time alone — never by the tier.

Timestamps are kept in the PAST and in whole seconds — see `test_ingest_reset_boundary`.
"""
import copy
import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.models import IngestEvent, SeriesState, UsageSeries
from app.parsing import offer_report
from app.services.cascade import SeriesFacts, build_cascade
from app.services.status import CONTRACT_VERSION, build_status
from tests.test_ingest_e2e import REAL, db, payload, send, utcnow  # noqa: F401

ULTRA = json.loads((Path(__file__).parent / "fixtures" / "usage_ultra_no_session.json")
                   .read_text("utf-8"))
SESSION_BUCKET = "bucket:five_hour"
SESSION_LIMIT = "limit:session|session|-|-"


def _t(seconds_ago=900):
    return utcnow().replace(microsecond=0) - timedelta(seconds=seconds_ago)


async def step(db, usage, *, at, raw=True, machine="desktop", is_backlog=False, sanitized=None):
    """One batch. `raw=True` sends `usage_raw` like a probe >= v15; `sanitized` replaces the
    merged `usage` (what `sanitize` made of the cache) while `usage_raw` keeps the original."""
    p = payload(usage=sanitized if sanitized is not None else copy.deepcopy(usage),
                captured_at=at)
    if raw:
        p["usage_raw"] = copy.deepcopy(usage)
    r = await send(db, p, machine=machine, is_backlog=is_backlog)
    await db.commit()
    return r


async def flag(db, key):
    return (await db.execute(
        select(SeriesState.not_offered_at).join(UsageSeries,
                                                UsageSeries.id == SeriesState.series_id)
        .where(UsageSeries.series_key == key))).scalar_one_or_none()


async def events(db, kind):
    return list((await db.execute(
        select(IngestEvent).where(IngestEvent.event_type == kind).order_by(IngestEvent.id)
    )).scalars().all())


async def card(db):
    s = json.loads((await build_status(db)).model_dump_json(by_alias=True))
    (a,) = s["accounts"]
    return s, a


def rung(a, key):
    return next(r for r in a["cascade"] if r["key"] == key)


def keys(a):
    return {s["seriesKey"] for s in a["series"]}


# --------------------------------------------------------------------------- parsing
def test_the_real_ultra_response_has_no_session_window():
    r = offer_report(ULTRA)
    assert r.not_offered(SESSION_BUCKET, "bucket")
    assert r.not_offered(SESSION_LIMIT, "limit")
    assert not r.not_offered("bucket:seven_day", "bucket")
    assert not r.not_offered("limit:weekly_all|weekly|-|-", "limit")
    assert not r.not_offered("limit:weekly_scoped|weekly|fable|-", "limit")


def test_an_idle_window_is_offered():
    r = offer_report(REAL | {"five_hour": {"utilization": 0, "resets_at": None}})
    assert not r.not_offered(SESSION_BUCKET, "bucket")


def test_an_absent_key_is_no_information():
    raw = {k: v for k, v in ULTRA.items() if k != "five_hour"}
    assert not offer_report(raw).not_offered(SESSION_BUCKET, "bucket")


def test_limits_that_say_nothing_give_no_information():
    for lim in (None, [], "x", [ULTRA["limits"][0], "not an object"]):
        raw = dict(ULTRA, limits=lim)
        assert offer_report(raw).limit_keys is None, lim
    raw = {k: v for k, v in ULTRA.items() if k != "limits"}
    assert offer_report(raw).limit_keys is None


def test_credit_pools_are_never_judged():
    r = offer_report(dict(ULTRA, spend=None, extra_usage=None))
    assert not r.not_offered("spend:org", "spend")
    assert not r.not_offered("extra:usage", "extra_usage")
    assert offer_report("not a dict") is None


# --------------------------------------------------------------------------- ingest
async def test_max_then_ultra_hides_the_session_and_turns_the_rung_off(db):
    t = _t()
    await step(db, REAL, at=t)
    r = await step(db, ULTRA, at=t + timedelta(seconds=60))

    assert r["series_not_offered"] == 2
    assert await flag(db, SESSION_BUCKET) == t + timedelta(seconds=60)
    assert await flag(db, SESSION_LIMIT) == t + timedelta(seconds=60)
    (ev,) = await events(db, "series_not_offered")
    assert ev.detail["series"] == sorted([SESSION_BUCKET, SESSION_LIMIT])
    assert ev.detail["last_utilization"][SESSION_BUCKET] == REAL["five_hour"]["utilization"]

    s, a = await card(db)
    assert s["contractVersion"] == CONTRACT_VERSION == 4
    assert SESSION_BUCKET not in keys(a) and SESSION_LIMIT not in keys(a)
    sess = rung(a, "session")
    assert sess["state"] == "off"
    assert sess["utilization"] is None and sess["seriesKey"] is None
    assert not sess["isCurrent"]


async def test_a_repeated_ultra_reading_moves_the_flag_without_a_second_event(db):
    t = _t()
    await step(db, REAL, at=t)
    await step(db, ULTRA, at=t + timedelta(seconds=60))
    r = await step(db, ULTRA, at=t + timedelta(seconds=120))

    assert r["series_not_offered"] == 0
    assert await flag(db, SESSION_BUCKET) == t + timedelta(seconds=120)
    assert len(await events(db, "series_not_offered")) == 1


async def test_an_older_ultra_reading_changes_nothing(db):
    t = _t()
    await step(db, REAL, at=t)
    await step(db, ULTRA, at=t - timedelta(seconds=60), machine="laptop")
    await step(db, ULTRA, at=t - timedelta(seconds=30), is_backlog=True)

    assert await flag(db, SESSION_BUCKET) is None
    assert not await events(db, "series_not_offered")


async def test_no_usage_raw_is_no_information(db):
    t = _t()
    await step(db, REAL, at=t)
    await step(db, ULTRA, at=t + timedelta(seconds=60), raw=False)
    assert await flag(db, SESSION_BUCKET) is None


async def test_a_window_nulled_by_sanitize_is_not_a_missing_window(db):
    """`sanitize` nulls an expired cached window in `usage`; `usage_raw` still has the dict."""
    t = _t()
    await step(db, REAL, at=t)
    nulled = copy.deepcopy(REAL)
    nulled["five_hour"] = None
    nulled["limits"] = [x for x in nulled["limits"] if x.get("kind") != "session"]
    await step(db, REAL, at=t + timedelta(seconds=60), sanitized=nulled)
    assert await flag(db, SESSION_BUCKET) is None
    assert await flag(db, SESSION_LIMIT) is None


async def test_a_newer_max_reading_brings_the_window_back(db):
    t = _t()
    await step(db, REAL, at=t)
    await step(db, ULTRA, at=t + timedelta(seconds=60))
    # A rise: a cached drop inside the same window would be held by the monotonicity guard,
    # and a held reading moves no state — the flag with it.
    back = copy.deepcopy(REAL)
    back["five_hour"]["utilization"] = 25.0
    r = await step(db, back, at=t + timedelta(seconds=120))

    assert r["series_offered"] >= 1
    assert await flag(db, SESSION_BUCKET) is None
    (ev,) = await events(db, "series_offered")
    assert SESSION_BUCKET in ev.detail["series"]
    _, a = await card(db)
    assert SESSION_BUCKET in keys(a) or SESSION_LIMIT in keys(a)
    assert rung(a, "session")["state"] == "on"


async def test_a_return_within_the_heartbeat_still_clears(db):
    """The value matches the frozen state, so dedup skips the sample — the window is back."""
    t = _t()
    await step(db, REAL, at=t)
    await step(db, ULTRA, at=t + timedelta(seconds=30))
    await step(db, REAL, at=t + timedelta(seconds=60))
    assert await flag(db, SESSION_BUCKET) is None
    assert await flag(db, SESSION_LIMIT) is None


async def test_a_stale_cache_between_two_readings_stays_hidden(db):
    """Another machine's cache dated after the last confirmation but before the newest
    not-offered reading moves the values, never the flag."""
    t = _t()
    await step(db, REAL, at=t)
    await step(db, ULTRA, at=t + timedelta(seconds=120))
    await step(db, REAL, at=t + timedelta(seconds=60), machine="laptop")
    assert await flag(db, SESSION_BUCKET) == t + timedelta(seconds=120)
    _, a = await card(db)
    assert rung(a, "session")["state"] == "off"


async def test_switching_plans_back_and_forth_follows_every_switch(db):
    t = _t(1800)
    plan = [REAL, ULTRA, REAL, ULTRA, REAL]
    for i, u in enumerate(plan):
        tweaked = copy.deepcopy(u)
        if tweaked.get("five_hour"):
            tweaked["five_hour"]["utilization"] = float(i)
        await step(db, tweaked, at=t + timedelta(seconds=60 * i))
        _, a = await card(db)
        want = "off" if u is ULTRA else "on"
        assert rung(a, "session")["state"] == want, "step %d" % i
    assert len(await events(db, "series_not_offered")) == 2
    assert len(await events(db, "series_offered")) == 2


async def test_an_account_first_seen_on_ultra_has_its_session_off(db):
    """The bucket series exists globally (another account had it); this account never did."""
    from tests.test_ingest_e2e import ACCOUNT_TEAM
    t = _t()
    p = payload(usage=copy.deepcopy(REAL), account=ACCOUNT_TEAM, captured_at=t)
    await send(db, p, machine="laptop")
    await db.commit()
    await step(db, ULTRA, at=t + timedelta(seconds=60))

    s = json.loads((await build_status(db)).model_dump_json(by_alias=True))
    ultra = next(a for a in s["accounts"] if a["orgType"] == "claude_max")
    assert rung(ultra, "session")["state"] == "off"
    assert SESSION_BUCKET not in keys(ultra)
    assert not await events(db, "series_not_offered")


async def test_an_install_that_only_ever_saw_ultra_has_its_session_off(db):
    """No account ever registered the session window: the null registers it, once.

    The only test that fails when the `_WINDOW_BUCKETS` filter or the existence check in
    `ingest_one` is dropped — the rest of the suite stays green on both."""
    t = _t()
    r = await step(db, ULTRA, at=t)

    # Only the rung windows: ULTRA nulls ~two dozen other keys, and none of them is a series.
    assert {k for k in r["series_registered"] if k.startswith("bucket:")} == {
        SESSION_BUCKET, "bucket:seven_day"}
    assert await flag(db, SESSION_BUCKET) is not None
    _, a = await card(db)
    assert rung(a, "session")["state"] == "off"
    assert SESSION_BUCKET not in keys(a)
    assert not await events(db, "series_not_offered")

    # The row exists now: the next Ultra reading registers nothing again.
    r = await step(db, ULTRA, at=t + timedelta(seconds=60))
    assert SESSION_BUCKET not in r["series_registered"]


# --------------------------------------------------------------------------- cascade
def _facts(*extra):
    return [SeriesFacts(series_key="bucket:seven_day", source="bucket", bucket_key="seven_day",
                        utilization=79.0),
            SeriesFacts(series_key="limit:weekly_all|weekly|-|-", source="limit",
                        kind="weekly_all", utilization=79.0, is_active=False), *extra]


def test_a_not_offered_session_is_off_and_the_weekly_is_current():
    frozen = SeriesFacts(series_key=SESSION_LIMIT, source="limit", kind="session",
                         offered=False)
    rungs = {r.key: r for r in build_cascade(_facts(frozen))}
    assert rungs["session"].state == "off"
    assert rungs["session"].utilization is None and rungs["session"].series_key is None
    assert rungs["weekly"].is_current


def test_one_session_candidate_still_offered_keeps_the_rung_on():
    rungs = {r.key: r for r in build_cascade(_facts(
        SeriesFacts(series_key=SESSION_LIMIT, source="limit", kind="session", offered=False),
        SeriesFacts(series_key=SESSION_BUCKET, source="bucket", bucket_key="five_hour",
                    utilization=12.0)))}
    assert rungs["session"].state == "on"
    assert rungs["session"].utilization == 12.0


def test_an_off_weekly_is_skipped_on_the_way_down():
    facts = [SeriesFacts(series_key="bucket:seven_day", source="bucket",
                         bucket_key="seven_day", offered=False),
             SeriesFacts(series_key=SESSION_BUCKET, source="bucket", bucket_key="five_hour",
                         utilization=100.0, is_active=True)]
    rungs = {r.key: r for r in build_cascade(facts)}
    assert rungs["weekly"].state == "off"
    assert not rungs["weekly"].is_current
