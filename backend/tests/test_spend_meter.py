"""Wyczerpany sufit organizacji, e2e: realny payload -> ingest -> series_state -> status.

Jednostkowe testy parsera i kaskady dostaja swieze fakty i przechodza nawet wtedy, gdy
sciezka zapisu jest zepsuta. Degradacja, ktora ten plik pilnuje, powstaje dopiero po
przejsciu przez `series_state` — dlatego wszystko tutaj idzie przez `ingest_one`.
"""
from datetime import timedelta

from sqlalchemy import func, select

from app.models import IngestEvent, LimitSample, SeriesState, UsageSeries
from app.services.ingest import ingest_one, utcnow
from app.services.status import build_status
from tests.team import USAGE_ACTIVE, USAGE_WITHDRAWN, team_payload
from tests.test_ingest_e2e import db  # noqa: F401  — wspolny silnik SQLite w pamieci

SPEND, EXTRA = "spend:org", "extra:usage"


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


# --------------------------------------------------------------------------- zapis
async def test_dedup_nie_pisze_wierszy_przez_cala_blokade(db):
    """Blokada trwa godzinami, a payload jest przez caly czas identyczny. Gdyby brak
    pomiaru zapisywal sie jak zmiana, tabela puchlaby przez cala awarie."""
    now = utcnow()
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
    """Konsekwencja przyjeta swiadomie: gdy PIERWSZY w zyciu pomiar trafia w blokade,
    seria nie ma zadnej wartosci i wypada z widoku (`status.py`, filtr `ever_non_null`).
    Informacja nie ginie — kaskada mowi wprost, ze kredyty sa wylaczone.

    Wazniejsze jest to, czego tu NIE MA: fantomowego zera z podpisem 'potwierdzone'."""
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
    """Gdy seria juz kiedys miala pomiar, blokada nie moze go wymazac — ostatnia ZMIERZONA
    wartosc zostaje, bo to jedyne, co o zuzyciu wiemy. Znika tylko udawanie, ze zmierzono
    ja teraz."""
    now = utcnow()
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
    # Probka MUSI powstac — inaczej historia klamie, ze wartosc trwala niezmiennie.
    assert await _samples(db, SPEND) == 2


async def test_spadek_do_braku_pomiaru_nie_jest_nieaktualnym_odczytem(db):
    """93% -> brak wartosci wyglada jak spadek, ale guard monotonicznosci porownuje liczby,
    a tu drugiej liczby nie ma. Gdyby to zliczyl jako `stale_read`, stan zamarlby na 93%
    az do konca blokady — czyli dokladnie ta cicha degradacja, ktora naprawiamy."""
    now = utcnow()
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


# --------------------------------------------------------------------------- kaskada e2e
async def test_kaskada_e2e_po_wycofaniu_nie_pokazuje_kredytow_jako_wlaczonych(db):
    """Jednostkowe `build_cascade` dostaje swieze fakty prosto z parsera i przechodzi
    nawet wtedy, gdy sciezka zapisu gubi powod. Degradacja powstaje dopiero w
    `series_state` — dlatego ten sam warunek ma tez wersje przez ingest."""
    now = utcnow()
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


# --------------------------------------------------------------------------- zdarzenia
async def _events(db):
    return (await db.execute(select(IngestEvent))).scalars().all()


async def test_wycofanie_i_powrot_zostawiaja_zdarzenia_z_poziomem_i_szczegolami(db):
    """Blokada nie rzuca bledem i nie zmienia niczego, co widac w liczbach — bez zdarzenia
    w logu nie ma po niej sladu. `warn`, bo to awaria po stronie organizacji; powrot to
    `info`, bo dobra wiadomosc nikogo nie budzi."""
    now = utcnow()
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
    """Blokada trwa godzinami. Zdarzenie opisuje PRZEJSCIE, nie stan — inaczej log tonie
    w setkach kopii tego samego zdania i przestaje sie nadawac do czytania."""
    now = utcnow()
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
    """Nie bylo z czego przejsc — konto po prostu tak wyglada od poczatku."""
    await ingest_one(db, machine_name="desktop", payload=team_payload(USAGE_WITHDRAWN))
    await db.commit()
    assert not [e for e in await _events(db) if e.event_type == "meter_withdrawn"]


# ------------------------------------------- sygnal spoza pasma nie przeslania pomiaru
async def test_powod_z_cache_klienta_nie_przeslania_danych_w_pasmie(db):
    """`cachedExtraUsageDisabledReason` mowi `org_spend_cap_reached` juz przy wyczerpanej
    WLASNEJ puli, gdy licznik dziala i podaje 100%. Gdyby ten sygnal przeslonil dane
    z pasma, wyzerowalby jedyna poprawna liczbe w najgorszym momencie. Werdykt zostaje
    na `usage.spend.disabled_reason` + `enabled`, bo tylko one sa wewnetrznie spojne
    z reszta tej samej odpowiedzi."""
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
