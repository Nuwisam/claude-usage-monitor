"""Exhausted organization ceiling, e2e: real payload -> ingest -> series_state -> status.

Unit tests of the parser and of the cascade get fresh facts and pass even when the write
path is broken. The degradation this file guards against arises only after passing
through `series_state` — which is why everything here goes through `ingest_one`.
"""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.models import IngestEvent, LimitSample, SeriesState, UsageSeries
from app.services.ingest import ingest_one, utcnow
from app.services.status import build_status
from tests.team import USAGE_ACTIVE, USAGE_WITHDRAWN, team_payload
from tests.test_ingest_e2e import db  # noqa: F401  — a shared in-memory SQLite engine

SPEND, EXTRA = "spend:org", "extra:usage"


def _baza():
    """A starting point in the PAST, with room for the longest scenario (6 min).

    A measurement must not be newer than the moment it arrived — `measured_at` clips it to
    the request's anchor. Intervals counted FORWARD from "now" would collapse into a single
    point and the tests would pass, checking neither dedup nor the meter's transitions."""
    return utcnow().replace(microsecond=0) - timedelta(minutes=15)


async def _samples(db, series_key: str) -> int:
    return (await db.execute(
        select(func.count()).select_from(LimitSample)
        .join(UsageSeries, UsageSeries.id == LimitSample.series_id)
        .where(UsageSeries.series_key == series_key)
    )).scalar_one()


async def _state(db, series_key: str) -> SeriesState:
    return (await db.execute(
        select(SeriesState).join(UsageSeries, UsageSeries.id == SeriesState.series_id)
        .where(UsageSeries.series_key == series_key)
    )).scalars().first()


def _cascade(card):
    return {r.key: r for r in card.cascade}


# --------------------------------------------------------------------------- write
async def test_dedup_nie_pisze_wierszy_przez_cala_blokade(db):
    """The block lasts for hours, and the payload is identical the whole time. If a missing
    measurement were written like a change, the table would swell for the whole outage."""
    now = _baza()
    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_WITHDRAWN, captured_at=now))
    await db.commit()
    po_pierwszym = await _samples(db, SPEND)

    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_WITHDRAWN,
                                          captured_at=now + timedelta(seconds=60)))
    await db.commit()
    assert await _samples(db, SPEND) == po_pierwszym
    assert await _samples(db, EXTRA) == po_pierwszym


async def test_konto_zapisane_pierwszy_raz_w_trakcie_blokady_nie_pokazuje_zera(db):
    """A consequence accepted deliberately: when the FIRST measurement ever lands in a block,
    the series has no value at all and drops out of the view (`status.py`, the `ever_non_null`
    filter). The information is not lost — the cascade says outright that credits are off.

    More important is what is NOT here: a phantom zero captioned 'confirmed'."""
    await ingest_one(db, machine_name="desktop", payload=team_payload(USAGE_WITHDRAWN))
    await db.commit()

    series = (await db.execute(
        select(UsageSeries).where(UsageSeries.series_key == SPEND)
    )).scalar_one()
    assert series.ever_non_null is False
    st = await _state(db, SPEND)
    assert st.last_utilization is None

    card = (await build_status(db)).accounts[0]
    assert SPEND not in {s.series_key for s in card.series}
    assert _cascade(card)["credits"].state == "off"


async def test_wycofanie_nie_kasuje_ostatniej_znanej_wartosci_serii(db):
    """When a series once had a measurement, a block must not erase it — the last MEASURED
    value stays, because it is the only thing we know about usage. All that goes away is
    the pretence that it was measured now."""
    now = _baza()
    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_ACTIVE, captured_at=now))
    await db.commit()
    assert float((await _state(db, SPEND)).last_utilization) == 93.0

    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_WITHDRAWN,
                                          captured_at=now + timedelta(seconds=120)))
    await db.commit()

    st = await _state(db, SPEND)
    assert st.last_utilization is None, "brak pomiaru zapisuje sie jako brak, nie jako 0"
    assert st.last_extra["disabled_reason"] == "org_level_disabled_until"
    # A sample MUST be created — otherwise the history lies that the value held unchanged.
    assert await _samples(db, SPEND) == 2


async def test_spadek_do_braku_pomiaru_nie_jest_nieaktualnym_odczytem(db):
    """93% -> no value looks like a drop, but the monotonicity guard compares numbers, and
    here the second number is missing. Were it counted as a `stale_read`, the state would
    freeze at 93% until the end of the block — exactly the silent degradation being fixed."""
    now = _baza()
    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_ACTIVE, captured_at=now))
    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_WITHDRAWN,
                                          captured_at=now + timedelta(seconds=120)))
    await db.commit()

    stale = (await db.execute(
        select(func.count()).select_from(LimitSample)
        .join(UsageSeries, UsageSeries.id == LimitSample.series_id)
        .where(UsageSeries.series_key == SPEND, LimitSample.stale_read.is_(True))
    )).scalar_one()
    assert stale == 0


# --------------------------------------------------------------------------- cascade e2e
async def test_kaskada_e2e_po_wycofaniu_nie_pokazuje_kredytow_jako_wlaczonych(db):
    """The unit-level `build_cascade` gets fresh facts straight from the parser and passes
    even when the write path loses the reason. The degradation arises only inside
    `series_state` — which is why the same condition also has a version through ingest."""
    now = _baza()
    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_ACTIVE, captured_at=now))
    await db.commit()
    assert _cascade((await build_status(db)).accounts[0])["credits"].state == "on"

    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_WITHDRAWN,
                                          captured_at=now + timedelta(seconds=120)))
    await db.commit()

    c = _cascade((await build_status(db)).accounts[0])
    assert c["credits"].state == "off"
    assert c["credits"].reason == "org_level_disabled_until"
    assert c["credits"].utilization is None
    assert c["hard_block"].limit_minor is None


# --------------------------------------------------------------------------- view
async def test_extra_usage_nie_rysuje_drugiego_wiersza_kredytow(db):
    """`spend` and `extra_usage` describe THE SAME pool, so one row must stand on the screen.

    The assertion on the numbers matters more here than the flag itself: 93.0 next to 92.656
    shows why pairing by VALUE (the one from the buckets) would never catch this pair —
    `spend.percent` arrives rounded to an int. Hence pairing by source.
    """
    await ingest_one(db, machine_name="desktop", payload=team_payload(USAGE_ACTIVE))
    await db.commit()

    by = {s.series_key: s for s in (await build_status(db)).accounts[0].series}
    assert by[SPEND].utilization == 93.0
    assert by[EXTRA].utilization == pytest.approx(92.656)

    assert by[SPEND].primary is True, "zwyciezca pary nie moze sie przewrocic"
    assert by[EXTRA].primary is False
    assert by[EXTRA].duplicate_of == SPEND


async def test_zgaszony_wiersz_nadal_niesie_ostatni_zmierzony_procent(db):
    """Dimming a ROW does not dim the SERIES. `duplicate_of` is a marker for the view, not a
    deletion: a dimmed `extra:usage` after the meter is withdrawn still holds the last
    MEASURED value and the reason, and the cascade gets its own reason independently (the
    facts are gathered BEFORE this filter).
    """
    now = _baza()
    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_ACTIVE, captured_at=now))
    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_WITHDRAWN,
                                          captured_at=now + timedelta(seconds=120)))
    await db.commit()

    card = (await build_status(db)).accounts[0]
    eu = {s.series_key: s for s in card.series}[EXTRA]
    assert eu.primary is False, "wiersz zgaszony"
    assert eu.unavailable_reason == "org_level_disabled_until"
    assert eu.raw_utilization == pytest.approx(92.656), "liczba przezyla zgaszenie wiersza"
    assert _cascade(card)["credits"].reason == "org_level_disabled_until"


# --------------------------------------------------------------------------- events
async def _events(db):
    return (await db.execute(select(IngestEvent))).scalars().all()


async def test_wycofanie_i_powrot_zostawiaja_zdarzenia_z_poziomem_i_szczegolami(db):
    """A block throws no error and changes nothing visible in the numbers — without an event
    in the log it leaves no trace at all. `warn`, because it is a failure on the
    organization's side; the return is `info`, because good news wakes nobody up."""
    now = _baza()
    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_ACTIVE, captured_at=now))
    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_WITHDRAWN,
                                          captured_at=now + timedelta(seconds=120)))
    await db.commit()

    w = [e for e in await _events(db) if e.event_type == "meter_withdrawn"]
    assert w, "wycofanie miernika nie zostawilo sladu"
    spend = [e for e in w if e.detail["series"] == SPEND][0]
    assert spend.level == "warn"
    assert spend.detail["reason"] == "org_level_disabled_until"
    assert spend.detail["last_utilization"] == 93.0, "ostatnia znana wartosc przed blokada"

    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_ACTIVE,
                                          captured_at=now + timedelta(seconds=240)))
    await db.commit()

    r = [e for e in await _events(db) if e.event_type == "meter_restored"]
    assert r and all(e.level == "info" for e in r)
    assert [e for e in r if e.detail["series"] == SPEND][0].detail["was_reason"] == \
        "org_level_disabled_until"


async def test_wycofanie_zapisuje_zdarzenie_raz_a_nie_przy_kazdym_pomiarze(db):
    """A block lasts for hours. The event describes a TRANSITION, not a state — otherwise
    the log drowns in hundreds of copies of the same sentence and stops being readable."""
    now = _baza()
    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_ACTIVE, captured_at=now))
    for i in range(5):
        await ingest_one(db, machine_name="desktop",
                         payload=team_payload(
                             USAGE_WITHDRAWN,
                             captured_at=now + timedelta(seconds=120 + 60 * i)))
    await db.commit()

    w = [e for e in await _events(db)
         if e.event_type == "meter_withdrawn" and e.detail["series"] == SPEND]
    assert len(w) == 1


async def test_pierwszy_pomiar_w_blokadzie_nie_zglasza_przejscia(db):
    """Nothing to transition from — the account simply looks like this from the start."""
    await ingest_one(db, machine_name="desktop", payload=team_payload(USAGE_WITHDRAWN))
    await db.commit()
    assert not [e for e in await _events(db) if e.event_type == "meter_withdrawn"]


# --------------------------------- an out-of-band signal does not eclipse a measurement
async def test_powod_z_cache_klienta_nie_przeslania_danych_w_pasmie(db):
    """`cachedExtraUsageDisabledReason` says `org_spend_cap_reached` already with the OWN
    pool exhausted, while the meter works and reports 100%. Were that signal to eclipse the
    in-band data, it would zero out the only correct number at the worst moment. The verdict
    stays with `usage.spend.disabled_reason` + `enabled`, because only those are internally
    consistent with the rest of that same response."""
    from tests.team import USAGE_POOL_EXHAUSTED

    await ingest_one(db, machine_name="desktop", payload=team_payload(
        USAGE_POOL_EXHAUSTED, extra_usage_disabled_reason="org_spend_cap_reached"))
    await db.commit()

    st = await _state(db, SPEND)
    assert float(st.last_utilization) == 100.0

    card = (await build_status(db)).accounts[0]
    spend = {s.series_key: s for s in card.series}[SPEND]
    assert spend.utilization == 100.0
    assert spend.unavailable_reason is None
    assert _cascade(card)["credits"].state == "on"
