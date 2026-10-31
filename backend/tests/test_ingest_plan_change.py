"""A fresh drop versus the monotonicity guard, and the plan fields beside it.

Observed live (account 1, 2026-10-27): the plan went `default_claude_max_5x` ->
`default_claude_max_20x`, and in the SAME request the three weekly series fell 67/67/84% -> 0%
with `resets_at` unchanged. The guard read that as a stale reading from another machine: the
samples went to the database with `stale_read=1` and `series_state` stood at the old values
until it was promoted by hand. It had happened before without any plan change (2026-09-16,
29 -> 22%; 2026-10-15, an early weekly reset 42 -> 0%). A provably identical window proves the
window did not roll over, not that the value cannot fall.

What the guard exists to stop is another machine's OLD CACHE. A fresh reading — one the probe
took from its own `/usage` dump (`measurement.fresh_covered`) — is not that, so the newest one
is accepted when it is lower by a whole point or more, with or without a plan change (by
less, it only confirms the state: the dump prints whole percents). A drop from the cache alone is
held, plan change or not: the owner switches between Pro, 5x and 20x several times a month,
so a rule keyed to the tier would either miss most switches or let a machine that missed one
push its stale cache through.

The AGENTS.md fixture rule (refresh `tests/fixtures/usage_max.json` when `parsing.py` or
`services/ingest.py` change) is deliberately not applied here. That file is the
`/api/oauth/usage` response and carries no tier at all: the tier arrives in the account block,
which the probe reads from `oauthAccount.organizationRateLimitTier` in `~/.claude.json`
(`client/usage-probe.py`). This change leaves the parsing of that response untouched, so a fresh
capture would test nothing new, and no capture of it could hold the 5x -> 20x step.
`ACCOUNT_20X` changes only the field the live account changed, on the shape of `ACCOUNT_MAX`.

Timestamps are kept in the PAST and in whole seconds — see `test_ingest_reset_boundary`.
"""
from datetime import timedelta

from sqlalchemy import select

from app.models import Account, IngestEvent
from tests.team import ACCOUNT_TEAM_REAL
from tests.test_ingest_e2e import ACCOUNT_MAX, db, payload, send, utcnow, with_util  # noqa: F401
from tests.test_ingest_reset_boundary import events, stale_flags, state

WEEK = "bucket:seven_day"
ACCOUNT_PRO = dict(ACCOUNT_MAX, org_rate_limit_tier="default_claude_pro")
ACCOUNT_20X = dict(ACCOUNT_MAX, org_rate_limit_tier="default_claude_max_20x")


def _t():
    return utcnow().replace(microsecond=0) - timedelta(seconds=600)


async def event_rows(db, event_type) -> list[IngestEvent]:
    return list((await db.execute(
        select(IngestEvent).where(IngestEvent.event_type == event_type)
        .order_by(IngestEvent.id)
    )).scalars().all())


async def account(db) -> Account:
    return (await db.execute(select(Account))).scalars().one()


async def step(db, *, week, at, fresh=False, acct=ACCOUNT_MAX, machine="desktop",
               is_backlog=False, subscription=None):
    """One batch. `fresh=True` dates the weekly bucket by the probe's own `/usage` dump,
    exactly as a probe with a usable dump sends it; `False` is a reading from the cache."""
    p = payload(usage=with_util(seven_day=week), account=acct, captured_at=at,
                fresh_at=at if fresh else None, fresh_covered=[WEEK] if fresh else None)
    if subscription is not None:
        p["token_meta"] = {"subscription_type": subscription}
    r = await send(db, p, machine=machine, is_backlog=is_backlog)
    await db.commit()
    return r


async def week_now(db) -> float:
    return float((await state(db, WEEK)).last_utilization)


async def test_fresh_drop_with_a_plan_change_is_accepted(db):
    """The incident itself: the tier change and a fresh drop arrive in one request."""
    t = _t()
    await step(db, week=67.0, at=t, fresh=True)
    r = await step(db, week=0.0, at=t + timedelta(seconds=60), fresh=True, acct=ACCOUNT_20X)

    st = await state(db, WEEK)
    assert float(st.last_utilization) == 0.0
    assert st.last_captured_at == t + timedelta(seconds=60)
    assert not any(await stale_flags(db, WEEK))
    assert r["guard_released"] == 1
    ev = await events(db)
    assert {"plan_changed", "guard_released"} <= ev
    assert "stale_read" not in ev
    (pc,) = await event_rows(db, "plan_changed")
    assert pc.batch_id == r["batch_id"], "the event belongs to the batch that saw it"
    assert pc.detail == {"org_rate_limit_tier": ["default_claude_max_5x",
                                                 "default_claude_max_20x"]}
    (gr,) = await event_rows(db, "guard_released")
    assert gr.batch_id == r["batch_id"] and gr.detail["series"] == [WEEK]
    assert gr.detail["plan_changed"] == pc.detail
    assert (await account(db)).org_rate_limit_tier == "default_claude_max_20x"


async def test_fresh_drop_without_a_plan_change_is_accepted(db):
    """2026-09-16 and 2026-10-15: Anthropic lowered the weekly figure with `resets_at`
    unchanged and no plan change at all. Fresh, it is the truth."""
    t = _t()
    await step(db, week=29.0, at=t, fresh=True)
    r = await step(db, week=22.0, at=t + timedelta(seconds=60), fresh=True)

    assert await week_now(db) == 22.0
    assert r["guard_released"] == 1
    assert "plan_changed" not in await events(db)
    (gr,) = await event_rows(db, "guard_released")
    assert gr.detail["plan_changed"] is None


async def test_cache_drop_is_held_even_with_a_plan_change(db):
    """Tier and cache come from the same machine: one that missed the change carries both
    stale, so a tier "change" in its batch proves nothing about its cached percentages."""
    t = _t()
    await step(db, week=67.0, at=t, fresh=True, acct=ACCOUNT_20X)
    r = await step(db, week=5.0, at=t + timedelta(seconds=60), acct=ACCOUNT_MAX,
                   machine="laptop")

    assert await week_now(db) == 67.0
    assert r["guard_released"] == 0
    assert (await stale_flags(db, WEEK))[-1] is True
    assert {"plan_changed", "stale_read"} <= await events(db)


async def test_frequent_plan_switching_releases_every_time(db):
    """Pro -> 5x -> 20x -> 5x -> Pro inside one window: every switch resets the pools, and
    every fresh drop after it lands — returning to a tier seen before included."""
    t = _t()
    plan = [(ACCOUNT_PRO, 80.0), (ACCOUNT_MAX, 0.0), (ACCOUNT_MAX, 40.0), (ACCOUNT_20X, 0.0),
            (ACCOUNT_20X, 30.0), (ACCOUNT_MAX, 0.0), (ACCOUNT_MAX, 50.0), (ACCOUNT_PRO, 0.0)]
    for i, (acct, week) in enumerate(plan):
        await step(db, week=week, at=t + timedelta(seconds=30 * i), fresh=True, acct=acct)
        assert await week_now(db) == week, "step %d (%s)" % (i, acct["org_rate_limit_tier"])
    assert len(await event_rows(db, "guard_released")) == 4
    assert not any(await stale_flags(db, WEEK))


async def test_whole_percent_dump_below_a_cached_fraction_confirms_the_state(db):
    """The dump prints whole percents, the cache fractions: a fresh 67 after a cached 67.6
    is the same figure rounded, not a drop. Written, it would cut the window in
    `window_start_index`; flagged, a fresh reading would be stale. It confirms the state,
    poll after poll, and writes nothing."""
    t = _t()
    await step(db, week=67.6, at=t)
    for i in (1, 2):
        at = t + timedelta(seconds=60 * i)
        r = await step(db, week=67.0, at=at, fresh=True)
        st = await state(db, WEEK)
        assert float(st.last_utilization) == 67.6
        assert st.last_confirmed_at == at
        assert r["guard_released"] == 0 and r["stale_reads"] == 0

    assert not any(await stale_flags(db, WEEK))
    assert {"guard_released", "stale_read"}.isdisjoint(await events(db))
    r = await step(db, week=66.0, at=t + timedelta(seconds=180), fresh=True)
    assert await week_now(db) == 66.0 and r["guard_released"] == 1


async def test_fresh_reading_older_than_the_state_stays_flagged(db):
    """`released` means "the state moved". An older reading does not write the state, and
    unflagged it would enter history and `window_start_index` as a drop."""
    t = _t()
    await step(db, week=67.0, at=t, fresh=True)
    r = await step(db, week=0.0, at=t - timedelta(seconds=60), fresh=True)

    assert await week_now(db) == 67.0
    assert (await stale_flags(db, WEEK))[-1] is True
    assert r["guard_released"] == 0
    assert "guard_released" not in await events(db)


async def test_guard_still_holds_a_cache_drop_from_another_machine(db):
    t = _t()
    await step(db, week=67.0, at=t, fresh=True)
    await step(db, week=3.0, at=t + timedelta(seconds=30), machine="laptop")

    assert await week_now(db) == 67.0
    assert (await stale_flags(db, WEEK))[-1] is True
    assert "stale_read" in await events(db)


async def test_backlog_entry_does_not_rewrite_plan_fields(db):
    """The spool holds hours-old entries and they arrive after the current record: taken
    here, an old tier flipped the field back with a spurious `plan_changed`."""
    t = _t()
    await step(db, week=67.0, at=t, acct=ACCOUNT_20X)
    r = await step(db, week=60.0, at=t + timedelta(seconds=60), acct=ACCOUNT_MAX,
                   is_backlog=True)

    assert r["ok"]
    assert (await account(db)).org_rate_limit_tier == "default_claude_max_20x"
    assert "plan_changed" not in await events(db)


async def test_backlog_entry_that_creates_the_account_still_describes_it(db):
    """The exception to the backlog rule: there is no other description to go by."""
    await step(db, week=10.0, at=_t(), acct=ACCOUNT_TEAM_REAL, is_backlog=True)

    a = await account(db)
    assert a.org_type == "claude_team"
    assert a.seat_tier == "team_tier_1"
    assert a.org_rate_limit_tier == "default_raven"


async def test_subscription_type_flip_emits_plan_changed(db):
    t = _t()
    await step(db, week=67.0, at=t, subscription="team")
    await step(db, week=67.0, at=t + timedelta(seconds=60), subscription="max")

    (pc,) = await event_rows(db, "plan_changed")
    assert pc.detail == {"subscription_type": ["team", "max"]}


async def test_first_report_of_a_tier_is_a_plan_changed_from_none(db):
    """None -> value is the first machine to report the field. It is recorded as what it is,
    and since the guard no longer keys on the tier, nothing else hangs on it."""
    t = _t()
    no_tier = {k: v for k, v in ACCOUNT_MAX.items() if k != "org_rate_limit_tier"}
    await step(db, week=67.0, at=t, acct=no_tier)
    await step(db, week=67.0, at=t + timedelta(seconds=60))

    (pc,) = await event_rows(db, "plan_changed")
    assert pc.detail == {"org_rate_limit_tier": [None, "default_claude_max_5x"]}
