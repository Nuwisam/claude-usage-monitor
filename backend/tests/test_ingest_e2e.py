"""Test end-to-end sciezki zapisu: realny payload -> ingest -> series_state -> /api/status.

Uzywa SQLite w pamieci. Nie pokrywa /api/history (zapytania downsamplingu sa specyficzne
dla MariaDB), za to pokrywa cala logike, ktora moze dac ZLE DANE: dedup, guard
monotonicznosci, wykrywanie przelaczenia konta i stany swiezosci.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Account, Base, IngestBatch, IngestEvent, LimitSample, SeriesState, UsageSeries,
)
from app.services.ingest import ingest_one, utcnow
from app.services.status import build_status

REAL = json.loads((Path(__file__).parent / "fixtures" / "usage_max.json").read_text("utf-8"))

ACCOUNT_MAX = {
    "uuid": "00000000-0000-4000-8000-000000000003",
    "email": "you@example.org", "display_name": "Tomasz",
    "org_uuid": "00000000-0000-4000-8000-000000000004",
    "org_type": "claude_max", "org_rate_limit_tier": "default_claude_max_5x",
    "seat_tier": None, "extra_usage_enabled": False,
}
ACCOUNT_TEAM = {
    "uuid": "aaaabbbb-0000-1111-2222-333344445555",
    "email": "praca@example.com", "org_type": "claude_team",
    "seat_tier": "premium", "org_rate_limit_tier": "team_premium",
}


_DOMYSLNE = object()


def payload(usage=None, account=_DOMYSLNE, captured_at=None, event="PostToolUse"):
    # sentinel, a nie `account or ACCOUNT_MAX` — pusty dict jest falszywy i podstawialby
    # konto domyslne, przez co test "payload bez konta" testowalby cos innego
    return {
        "account": ACCOUNT_MAX if account is _DOMYSLNE else account,
        "token_meta": {"subscription_type": "max"},
        "captured_at": (captured_at or utcnow()).isoformat(),
        "client": {"host": "DESKTOP-X", "cc_version": "2.1.215",
                   "script_version": 1, "exec_ms": 337},
        "hook": {"event": event, "session_id": "sess-1", "cwd": "z:/projects/x"},
        "http": {"status": 200, "request_id": "req_test"},
        "usage": usage if usage is not None else REAL,
    }


def with_util(five_hour=None, seven_day=None):
    u = json.loads(json.dumps(REAL))
    if five_hour is not None:
        u["five_hour"]["utilization"] = five_hour
    if seven_day is not None:
        u["seven_day"]["utilization"] = seven_day
    return u


@pytest_asyncio.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def count(db, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


# --------------------------------------------------------------------------- podstawy
async def test_pierwszy_pomiar_tworzy_konto_serie_i_probki(db):
    r = await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    assert r["ok"] and r["samples_written"] > 0

    acc = (await db.execute(select(Account))).scalars().one()
    assert acc.account_uuid == ACCOUNT_MAX["uuid"]
    assert acc.org_type == "claude_max"
    assert acc.subscription_type == "max"

    # 3 niepuste buckety (five_hour, seven_day) + 3 limits + extra_usage + spend
    keys = {s.series_key for s in (await db.execute(select(UsageSeries))).scalars()}
    assert "bucket:five_hour" in keys
    assert "spend:org" in keys, "spend musi byc seria — na Team to JEST wiazacy limit"
    assert any(k.startswith("limit:weekly_all") for k in keys)


async def test_puste_buckety_nie_tworza_probek(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    keys = {s.series_key for s in (await db.execute(select(UsageSeries))).scalars()}
    assert "bucket:seven_day_opus" not in keys       # null w realnym payloadzie


async def test_surowy_payload_jest_zachowany(db):
    r = await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    b = (await db.execute(select(IngestBatch).where(IngestBatch.id == r["batch_id"]))).scalar_one()
    assert b.raw_payload_id is not None and b.payload_sha256


# --------------------------------------------------------------------------- dedup
async def test_dedup_nie_pisze_identycznych_wierszy(db):
    now = utcnow()
    await ingest_one(db, machine_name="desktop", payload=payload(captured_at=now))
    po_pierwszym = await count(db, LimitSample)
    # ten sam payload 60 s pozniej — ponizej heartbeatu (300 s)
    await ingest_one(db, machine_name="desktop",
                     payload=payload(captured_at=now + timedelta(seconds=60)))
    await db.commit()
    assert await count(db, LimitSample) == po_pierwszym, "identyczna wartosc nie moze dublowac wierszy"


async def test_dedup_dziala_gdy_zmienily_sie_TYLKO_mikrosekundy_resets_at(db):
    """Regresja na realny blad, ktory przez dobe cicho zabijal dedup.

    Powyzszy test uzywa fixture'a, wiec `resets_at` jest bajt-identyczny i dedup
    "dzialal". W naturze Anthropic stempluje `resets_at` mikrosekundami SWOJEJ
    ODPOWIEDZI, wiec kazda probka miala inna wartosc, porownanie zawsze wypadalo
    "zmienilo sie" i kazdy pomiar szedl do bazy jako nowy wiersz. Zmierzone: 63 probki
    w 6 h, 63 rozne `resets_at`, przy zaledwie 10 roznych wartosciach utilization.
    """
    now = utcnow()
    u1 = json.loads(json.dumps(REAL))
    u1["five_hour"]["resets_at"] = "2026-07-27T00:59:59.056340+00:00"
    u1["seven_day"]["resets_at"] = "2026-08-01T15:59:59.056361+00:00"
    await ingest_one(db, machine_name="desktop", payload=payload(usage=u1, captured_at=now))
    po_pierwszym = await count(db, LimitSample)

    # ta sama granica okna, inne mikrosekundy odpowiedzi — to NIE jest zmiana danych
    u2 = json.loads(json.dumps(u1))
    u2["five_hour"]["resets_at"] = "2026-07-27T00:59:59.981119+00:00"
    u2["seven_day"]["resets_at"] = "2026-08-01T15:59:59.998004+00:00"
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=u2, captured_at=now + timedelta(seconds=60)))
    await db.commit()

    assert await count(db, LimitSample) == po_pierwszym, \
        "mikrosekundy odpowiedzi nie sa zmiana wartosci i nie moga tworzyc wierszy"


async def test_guard_monotonicznosci_dziala_przy_szumie_mikrosekund(db):
    """Ten sam blad wylaczal guard monotonicznosci: odpalal sie tylko przy NIEZMIENIONYM
    `resets_at`, a ten zmienial sie zawsze. Efekt byl grozniejszy niz spuchnieta tabela —
    nieaktualny odczyt z drugiej maszyny mogl cofnac `series_state` i widok Live."""
    now = utcnow()
    u1 = json.loads(json.dumps(REAL))
    u1["five_hour"]["utilization"] = 60.0
    u1["five_hour"]["resets_at"] = "2026-07-27T00:59:59.111111+00:00"
    await ingest_one(db, machine_name="desktop", payload=payload(usage=u1, captured_at=now))

    u2 = json.loads(json.dumps(u1))
    u2["five_hour"]["utilization"] = 40.0      # spadek bez zmiany okna => stary odczyt
    u2["five_hour"]["resets_at"] = "2026-07-27T00:59:59.999999+00:00"
    await ingest_one(db, machine_name="laptop",
                     payload=payload(usage=u2, captured_at=now + timedelta(seconds=30)))
    await db.commit()

    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "stale_read" in types, "spadek przy tej samej granicy okna to nieaktualny odczyt"

    st = (await db.execute(
        select(SeriesState).join(UsageSeries, UsageSeries.id == SeriesState.series_id)
        .where(UsageSeries.series_key == "bucket:five_hour")
    )).scalars().first()
    assert st is not None and float(st.last_utilization) == 60.0, "stan nie moze sie cofnac"


async def test_heartbeat_zapisuje_mimo_braku_zmiany(db, monkeypatch):
    """Guard skew zegara odrzucilby captured_at 400 s w przyszlosci — i slusznie, bo klient
    nie moze raportowac przyszlosci. Zeby przetestowac SAM heartbeat, luzujemy tolerancje."""
    from app.config import settings
    monkeypatch.setattr(settings, "clock_skew_tolerance_sec", 86400)

    now = utcnow()
    await ingest_one(db, machine_name="desktop",
                     payload=payload(captured_at=now - timedelta(seconds=400)))
    n1 = await count(db, LimitSample)
    await ingest_one(db, machine_name="desktop", payload=payload(captured_at=now))
    await db.commit()
    assert await count(db, LimitSample) > n1


async def test_captured_at_w_przyszlosci_jest_korygowany(db):
    """Regresja na powyzsze: bez luzowania tolerancji przyszly timestamp MA byc odrzucony."""
    now = utcnow()
    await ingest_one(db, machine_name="desktop",
                     payload=payload(captured_at=now + timedelta(seconds=400)))
    await db.commit()
    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "clock_skew" in types


async def test_zmiana_wartosci_zawsze_zapisuje(db):
    now = utcnow()
    await ingest_one(db, machine_name="desktop", payload=payload(captured_at=now))
    n1 = await count(db, LimitSample)
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=with_util(five_hour=12.0),
                                     captured_at=now + timedelta(seconds=30)))
    await db.commit()
    assert await count(db, LimitSample) == n1 + 1


# --------------------------------------------------------------------------- guard
async def test_nieaktualny_odczyt_nie_cofa_stanu(db):
    """Dwie maszyny na tym samym koncie maja WLASNY cache. Starszy odczyt z maszyny B
    nie moze cofnac wskaznika — wygladaloby to jak reset okna."""
    now = utcnow()
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=with_util(five_hour=40.0), captured_at=now))
    await db.commit()

    await ingest_one(db, machine_name="laptop",
                     payload=payload(usage=with_util(five_hour=25.0),
                                     captured_at=now + timedelta(seconds=30)))
    await db.commit()

    series = (await db.execute(
        select(UsageSeries).where(UsageSeries.series_key == "bucket:five_hour")
    )).scalar_one()
    st = (await db.execute(
        select(SeriesState).where(SeriesState.series_id == series.id)
    )).scalar_one()
    assert float(st.last_utilization) == 40.0, "stan biezacy nie moze sie cofnac"

    stale = (await db.execute(
        select(LimitSample).where(LimitSample.stale_read.is_(True))
    )).scalars().all()
    assert stale, "nieaktualny odczyt ma byc zapisany, tylko oflagowany"


async def test_realny_spadek_przy_zmianie_resets_at_przechodzi(db):
    """Spadek przy ZMIENIONYM resets_at to prawdziwy reset okna, nie nieaktualny odczyt."""
    now = utcnow()
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=with_util(five_hour=90.0), captured_at=now))
    u = with_util(five_hour=3.0)
    u["five_hour"]["resets_at"] = "2026-07-27T01:00:00.000000+00:00"
    await ingest_one(db, machine_name="desktop",
                     payload=payload(usage=u, captured_at=now + timedelta(seconds=30)))
    await db.commit()

    series = (await db.execute(
        select(UsageSeries).where(UsageSeries.series_key == "bucket:five_hour")
    )).scalar_one()
    st = (await db.execute(
        select(SeriesState).where(SeriesState.series_id == series.id)
    )).scalar_one()
    assert float(st.last_utilization) == 3.0


# --------------------------------------------------------------------------- konta
async def test_przelaczenie_konta_jest_wykrywane(db):
    await ingest_one(db, machine_name="desktop", payload=payload(account=ACCOUNT_MAX))
    await db.commit()
    await ingest_one(db, machine_name="desktop", payload=payload(account=ACCOUNT_TEAM))
    await db.commit()

    assert await count(db, Account) == 2
    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "account_switched" in types
    assert "new_account_for_token" in types


async def test_team_zachowuje_seat_tier(db):
    await ingest_one(db, machine_name="desktop", payload=payload(account=ACCOUNT_TEAM))
    await db.commit()
    a = (await db.execute(
        select(Account).where(Account.account_uuid == ACCOUNT_TEAM["uuid"])
    )).scalar_one()
    assert a.org_type == "claude_team" and a.seat_tier == "premium"


async def test_payload_bez_konta_jest_odrzucany(db):
    r = await ingest_one(db, machine_name="desktop", payload=payload(account={}))
    await db.commit()
    assert not r["ok"] and r["samples_written"] == 0
    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "no_oauth_account" in types


# --------------------------------------------------------------------------- drift
async def test_nowy_bucket_rejestruje_serie_i_zglasza(db):
    u = json.loads(json.dumps(REAL))
    u["calkiem_nowy_limit"] = {"utilization": 55.5, "resets_at": "2026-08-02T00:00:00+00:00"}
    r = await ingest_one(db, machine_name="desktop", payload=payload(usage=u))
    await db.commit()
    assert "bucket:calkiem_nowy_limit" in r["series_registered"]


async def test_uszkodzone_pole_nie_przerywa_pomiaru(db):
    u = json.loads(json.dumps(REAL))
    u["five_hour"] = "nagle string"
    r = await ingest_one(db, machine_name="desktop", payload=payload(usage=u))
    await db.commit()
    assert r["ok"] and r["samples_written"] > 0     # reszta serii przeszla
    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "schema_drift" in types


async def test_rozjechany_zegar_klienta_jest_korygowany(db):
    stary = utcnow() - timedelta(hours=5)
    await ingest_one(db, machine_name="desktop", payload=payload(captured_at=stary))
    await db.commit()
    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "clock_skew" in types


# --------------------------------------------------------------------------- status
async def test_status_pokazuje_konto_z_planem_i_aktywnym_limitem(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    st = await build_status(db)

    assert st.contract_version == 2
    assert len(st.accounts) == 1
    a = st.accounts[0]
    assert a.org_type == "claude_max"
    assert a.rate_limit_tier == "default_claude_max_5x"

    by = {s.series_key: s for s in a.series}
    assert by["bucket:five_hour"].freshness == "live"
    assert by["bucket:five_hour"].utilization == pytest.approx(REAL["five_hour"]["utilization"])

    aktywne = [s for s in a.series if s.is_active]
    assert len(aktywne) == 1
    assert aktywne[0].kind == "weekly_all"

    # Semantyka serii, bez ktorej UI musialoby zgadywac, ktora seria jest oknem 5 h.
    assert by["bucket:five_hour"].bucket_key == "five_hour"
    sesja = [s for s in a.series if s.kind == "session"]
    assert len(sesja) == 1 and sesja[0].group == "session"


async def test_duplikaty_bucket_limit_sa_wykrywane_z_danych(db):
    """API podaje ten sam limit dwa razy: jako bucket i jako wpis w limits[].
    Parujemy po danych, nie po zahardkodowanej mapie — wpis z limits[] wygrywa,
    bo niesie is_active i severity."""
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    st = await build_status(db)
    by = {s.series_key: s for s in st.accounts[0].series}

    assert by["bucket:five_hour"].primary is False
    assert by["bucket:five_hour"].duplicate_of.startswith("limit:session")
    assert by["bucket:seven_day"].primary is False
    # wpisy z limits[] pozostaja glowne
    assert all(s.primary for s in st.accounts[0].series if s.source == "limit")
    # spend nie ma odpowiednika w limits[] i musi zostac glownym
    assert by["spend:org"].primary is True


async def test_rozjazd_wartosci_nie_jest_traktowany_jak_duplikat(db):
    """Repo referencyjne opisuje, ze nowsze odpowiedzi zeruja starsze pola per-model.
    Gdy wartosci sie rozjada, obie serie maja zostac widoczne — wolimy pokazac rozjazd
    niz go ukryc."""
    u = json.loads(json.dumps(REAL))
    u["five_hour"]["utilization"] = 77.0          # bucket mowi co innego niz limits[]
    await ingest_one(db, machine_name="desktop", payload=payload(usage=u))
    await db.commit()
    st = await build_status(db)
    by = {s.series_key: s for s in st.accounts[0].series}
    assert by["bucket:five_hour"].primary is True


async def test_status_nie_pokazuje_pustych_serii(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    st = await build_status(db)
    assert all(s.raw_utilization is not None or s.freshness != "live"
               for s in st.accounts[0].series)
