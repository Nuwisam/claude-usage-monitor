"""End-to-end test of the write path: real payload -> ingest -> series_state -> /api/status.

Uses in-memory SQLite. Does not cover /api/history (the downsampling queries are specific
to MariaDB), but does cover all the logic that can yield BAD DATA: dedup, the monotonicity
guard, account-switch detection and freshness states.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import (
    Account, Base, IngestBatch, IngestEvent, LimitSample, SeriesState, UsageSeries,
)
from app.services.ingest import ingest_one, request_offset, utcnow
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


def payload(usage=None, account=_DOMYSLNE, captured_at=None, event="PostToolUse",
            sent_at=None, fresh_at=None, fresh_covered=None):
    # a sentinel, not `account or ACCOUNT_MAX` — an empty dict is falsy and would put
    # the default account in its place, making the test "payload with no account" test
    # something else
    meas = {"source": "cli_merged", "cache_age_s": 42, "fresh_age_s": 7}
    # `sent_at` is NOT added here by default — `send` appends it at the moment of sending,
    # exactly like the probe (client/usage-probe.py, just before `post`). Passing it here
    # explicitly serves to model a SKEWED client clock.
    if sent_at is not None:
        meas["sent_at"] = sent_at.isoformat()
    if fresh_at is not None:
        meas["fresh_at"] = fresh_at.isoformat()
    if fresh_covered is not None:
        meas["fresh_covered"] = fresh_covered
    return {
        "account": ACCOUNT_MAX if account is _DOMYSLNE else account,
        "token_meta": {"subscription_type": "max"},
        "captured_at": (captured_at or utcnow()).isoformat(),
        "client": {"host": "DESKTOP-X", "script_version": 5, "exec_ms": 36},
        "hook": {"event": event, "session_id": "sess-1", "cwd": "z:/projects/x"},
        "measurement": meas,
        "usage": usage if usage is not None else REAL,
    }


async def send(db, p, *, machine="desktop", arrived_at=None, is_backlog=False):
    """Repeats what the handler does: ONE time anchor per request, the offset taken from
    `measurement.sent_at` and computed by the same function the shipped code uses.

    When a payload has no `sent_at`, one equal to the anchor is added — that models a client
    with a synchronized clock, whose offset comes out zero and whose measurement stamp stays
    where it was put. A payload with an explicit `sent_at` models skew."""
    # Whole seconds, because `parse_ts` truncates to seconds — otherwise the offset would
    # come out as a fraction of a second and break assertions on exact stamp equality.
    arrived_at = (arrived_at or utcnow()).replace(microsecond=0)
    meas = p.get("measurement")
    if isinstance(meas, dict) and "sent_at" not in meas:
        meas["sent_at"] = arrived_at.isoformat()
    return await ingest_one(db, machine_name=machine, payload=p, arrived_at=arrived_at,
                            offset=request_offset(p, arrived_at), is_backlog=is_backlog)


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


# ----------------------------------------------------------------------------- basics
async def test_pierwszy_pomiar_tworzy_konto_serie_i_probki(db):
    r = await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    assert r["ok"] and r["samples_written"] > 0

    acc = (await db.execute(select(Account))).scalars().one()
    assert acc.account_uuid == ACCOUNT_MAX["uuid"]
    assert acc.org_type == "claude_max"
    assert acc.subscription_type == "max"

    # 3 non-empty buckets (five_hour, seven_day) + 3 limits + extra_usage + spend
    keys = {s.series_key for s in (await db.execute(select(UsageSeries))).scalars()}
    assert "bucket:five_hour" in keys
    assert "spend:org" in keys, "spend musi byc seria — na Team to JEST wiazacy limit"
    assert any(k.startswith("limit:weekly_all") for k in keys)


async def test_puste_buckety_nie_tworza_probek(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    keys = {s.series_key for s in (await db.execute(select(UsageSeries))).scalars()}
    assert "bucket:seven_day_opus" not in keys       # null in the real payload


async def test_surowy_payload_jest_zachowany(db):
    r = await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    b = (await db.execute(select(IngestBatch).where(IngestBatch.id == r["batch_id"]))).scalar_one()
    assert b.raw_payload_id is not None and b.payload_sha256


# --------------------------------------------------------------------------- dedup
# Measurement stamps lie in the PAST, because `measured_at` clamps them to `arrived_at`:
# a measurement cannot be newer than the moment it arrived. Intervals counted from the
# future would collapse to a single point and the test would pass while checking nothing.
def _t0(sekund_wstecz=600):
    return utcnow().replace(microsecond=0) - timedelta(seconds=sekund_wstecz)


async def test_dedup_nie_pisze_identycznych_wierszy(db):
    now = _t0()
    await send(db, payload(captured_at=now))
    po_pierwszym = await count(db, LimitSample)
    # the same payload 60 s later — below the heartbeat (300 s)
    await send(db, payload(captured_at=now + timedelta(seconds=60)))
    await db.commit()
    assert await count(db, LimitSample) == po_pierwszym, "identyczna wartosc nie moze dublowac wierszy"


async def test_dedup_dziala_gdy_zmienily_sie_TYLKO_mikrosekundy_resets_at(db):
    """Regression on a real defect that quietly killed dedup for a full day.

    The test above uses a fixture, so `resets_at` is byte-identical and dedup
    "worked". In the wild Anthropic stamps `resets_at` with the microseconds of ITS
    OWN RESPONSE, so every sample had a different value, the comparison always came out
    "it changed" and every measurement went to the database as a new row. Measured: 63
    samples in 6 h, 63 different `resets_at`, with only 10 distinct utilization values.
    """
    now = _t0()
    u1 = json.loads(json.dumps(REAL))
    u1["five_hour"]["resets_at"] = "2026-07-27T00:59:59.056340+00:00"
    u1["seven_day"]["resets_at"] = "2026-08-01T15:59:59.056361+00:00"
    await send(db, payload(usage=u1, captured_at=now))
    po_pierwszym = await count(db, LimitSample)

    # the same window boundary, different response microseconds — NOT a data change
    u2 = json.loads(json.dumps(u1))
    u2["five_hour"]["resets_at"] = "2026-07-27T00:59:59.981119+00:00"
    u2["seven_day"]["resets_at"] = "2026-08-01T15:59:59.998004+00:00"
    await send(db, payload(usage=u2, captured_at=now + timedelta(seconds=60)))
    await db.commit()

    assert await count(db, LimitSample) == po_pierwszym, \
        "mikrosekundy odpowiedzi nie sa zmiana wartosci i nie moga tworzyc wierszy"


async def test_guard_monotonicznosci_dziala_przy_szumie_mikrosekund(db):
    """The same defect disabled the monotonicity guard: it fired only on an UNCHANGED
    `resets_at`, and that changed every time. The effect was worse than a bloated table —
    a stale reading from a second machine could roll `series_state` and the Live view
    back."""
    now = _t0()
    u1 = json.loads(json.dumps(REAL))
    u1["five_hour"]["utilization"] = 60.0
    u1["five_hour"]["resets_at"] = "2026-07-27T00:59:59.111111+00:00"
    await send(db, payload(usage=u1, captured_at=now))

    u2 = json.loads(json.dumps(u1))
    u2["five_hour"]["utilization"] = 40.0      # a drop with no window change => old reading
    u2["five_hour"]["resets_at"] = "2026-07-27T00:59:59.999999+00:00"
    await send(db, payload(usage=u2, captured_at=now + timedelta(seconds=30)),
               machine="laptop")
    await db.commit()

    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "stale_read" in types, "spadek przy tej samej granicy okna to nieaktualny odczyt"

    st = (await db.execute(
        select(SeriesState).join(UsageSeries, UsageSeries.id == SeriesState.series_id)
        .where(UsageSeries.series_key == "bucket:five_hour")
    )).scalars().first()
    assert st is not None and float(st.last_utilization) == 60.0, "stan nie moze sie cofnac"


async def test_heartbeat_zapisuje_mimo_braku_zmiany(db):
    """A gap larger than the heartbeat (300 s) writes a sample even though the value did
    not change.

    Stamps no longer have to fall within any skew tolerance: the server dates a measurement
    from the age reported by the client, so an old stamp is simply an old measurement."""
    now = _t0()
    await send(db, payload(captured_at=now - timedelta(seconds=400)))
    n1 = await count(db, LimitSample)
    await send(db, payload(captured_at=now))
    await db.commit()
    assert await count(db, LimitSample) > n1


async def test_captured_at_w_przyszlosci_jest_przycinany(db):
    """A payload WITHOUT `sent_at` (a probe below v5): the offset is zero, so the client's
    stamp goes through untouched — except that it cannot be newer than the moment it
    arrived. Previously the server time was substituted; the effect is the same, but only
    by accident, because that same substitution ALSO rejuvenated stamps 400 s in the past."""
    arrived = utcnow().replace(microsecond=0)
    await ingest_one(db, machine_name="desktop", arrived_at=arrived,
                     payload=payload(captured_at=arrived + timedelta(seconds=400)))
    await db.commit()
    stamps = {s.captured_at for s in (await db.execute(select(LimitSample))).scalars()}
    assert stamps and max(stamps) <= arrived


async def test_pomiar_datowany_po_wysylce_jest_odrzucany(db):
    """The client clock moved backwards between recording the measurement and sending it.
    `sent_at - ts` is then negative, i.e. the measurement supposedly came into being AFTER
    the send — the dating is unreliable, and the stamp would land on the request's anchor
    and pass `newest`, overwriting the state. The whole entry is rejected; the raw payload
    goes to the database anyway (rule 6)."""
    arrived = utcnow().replace(microsecond=0)
    r = await send(db, payload(captured_at=arrived + timedelta(seconds=90),
                               sent_at=arrived), arrived_at=arrived)
    await db.commit()
    assert not r["ok"] and r["samples_written"] == 0
    assert await count(db, LimitSample) == 0
    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "clock_backwards" in types
    b = (await db.execute(select(IngestBatch))).scalars().one()
    assert b.raw_payload_id is not None, "surowy payload musi zostac zapisany"


async def test_zmiana_wartosci_zawsze_zapisuje(db):
    now = _t0()
    await send(db, payload(captured_at=now))
    n1 = await count(db, LimitSample)
    await send(db, payload(usage=with_util(five_hour=12.0),
                           captured_at=now + timedelta(seconds=30)))
    await db.commit()
    assert await count(db, LimitSample) == n1 + 1


# --------------------------------------------------------------------------- guard
async def test_nieaktualny_odczyt_nie_cofa_stanu(db):
    """Two machines on the same account each have their OWN cache. An older reading from
    machine B must not roll the indicator back — it would look like a window reset."""
    now = _t0()
    await send(db, payload(usage=with_util(five_hour=40.0), captured_at=now))
    await db.commit()

    await send(db, payload(usage=with_util(five_hour=25.0),
                           captured_at=now + timedelta(seconds=30)), machine="laptop")
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
    """A drop with a CHANGED resets_at is a real window reset, not a stale reading."""
    now = _t0()
    await send(db, payload(usage=with_util(five_hour=90.0), captured_at=now))
    u = with_util(five_hour=3.0)
    u["five_hour"]["resets_at"] = "2026-07-27T01:00:00.000000+00:00"
    await send(db, payload(usage=u, captured_at=now + timedelta(seconds=30)))
    await db.commit()

    series = (await db.execute(
        select(UsageSeries).where(UsageSeries.series_key == "bucket:five_hour")
    )).scalar_one()
    st = (await db.execute(
        select(SeriesState).where(SeriesState.series_id == series.id)
    )).scalar_one()
    assert float(st.last_utilization) == 3.0


# ------------------------------------------------------------------------ accounts
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
    assert r["ok"] and r["samples_written"] > 0     # the other series got through
    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "schema_drift" in types


async def test_rozjechany_zegar_klienta_nie_psuje_datowania(db):
    """The client clock is 5 h behind. The measurement is 120 s old on ITS clock — and must
    be just as old after the write, because age is a difference within one clock while the
    anchor is the server's.

    This inverts the old behavior: on such a skew `resolve_captured_at` substituted the
    server time for the moment of MEASUREMENT, i.e. it claimed the value had been measured
    a moment ago. The event stays — as diagnostics, with no influence on the write."""
    arrived = utcnow().replace(microsecond=0)
    zegar_klienta = arrived - timedelta(hours=5)
    await send(db, payload(captured_at=zegar_klienta - timedelta(seconds=120),
                           sent_at=zegar_klienta), arrived_at=arrived)
    await db.commit()

    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "clock_skew" in types
    stamps = {s.captured_at for s in (await db.execute(select(LimitSample))).scalars()}
    assert stamps == {arrived - timedelta(seconds=120)}

    b = (await db.execute(select(IngestBatch))).scalars().one()
    assert b.clock_offset_s == 5 * 3600, "offset musi byc odtwarzalny z bazy"


# --------------------------------------------------------------------------- status
async def test_status_pokazuje_konto_z_planem_i_aktywnym_limitem(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    st = await build_status(db)

    assert st.contract_version == 3
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

    # Series semantics, without which the UI would have to guess which series is the
    # 5 h window.
    assert by["bucket:five_hour"].bucket_key == "five_hour"
    sesja = [s for s in a.series if s.kind == "session"]
    assert len(sesja) == 1 and sesja[0].group == "session"


async def test_duplikaty_bucket_limit_sa_wykrywane_z_danych(db):
    """The API reports the same limit twice: as a bucket and as an entry in limits[].
    Pairing goes by the data, not by a hardcoded map — the limits[] entry wins,
    because it carries is_active and severity."""
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    st = await build_status(db)
    by = {s.series_key: s for s in st.accounts[0].series}

    assert by["bucket:five_hour"].primary is False
    assert by["bucket:five_hour"].duplicate_of.startswith("limit:session")
    assert by["bucket:seven_day"].primary is False
    # limits[] entries stay primary
    assert all(s.primary for s in st.accounts[0].series if s.source == "limit")
    # spend has no counterpart in limits[] and must stay primary
    assert by["spend:org"].primary is True


async def test_rozjazd_wartosci_nie_jest_traktowany_jak_duplikat(db):
    """The reference repo documents that newer responses zero out older per-model fields.
    When the values diverge, both series must stay visible — showing the divergence is
    better than hiding it."""
    u = json.loads(json.dumps(REAL))
    u["five_hour"]["utilization"] = 77.0          # the bucket disagrees with limits[]
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


# -------------------------------------------------- freshness vs value change (v3)
async def _five_hour_state(db):
    row = (await db.execute(
        select(SeriesState).join(UsageSeries, UsageSeries.id == SeriesState.series_id)
        .where(UsageSeries.series_key == "bucket:five_hour")
    )).scalar_one()
    return row


async def test_niezmienna_wartosc_nadal_odswieza_potwierdzenie(db):
    """The heart of the matter: dedup writes no sample when the value has not changed. Were
    freshness computed from the time of the last SAMPLE, a stable reading would start to
    look like a broken connection after 5 minutes — even though the client reports every
    60 s."""
    t0 = _t0(600)
    await send(db, payload(captured_at=t0))
    await db.commit()
    st0 = await _five_hour_state(db)
    assert st0.last_captured_at == st0.last_confirmed_at == t0

    # the same value, measured 230 s later — still below the heartbeat threshold (300 s)
    t1 = t0 + timedelta(seconds=230)
    await send(db, payload(captured_at=t1))
    await db.commit()

    st1 = await _five_hour_state(db)
    assert st1.last_captured_at == t0, "probka celowo NIE zostala zapisana (dedup)"
    assert st1.last_confirmed_at == t1, "ale pomiar sie odbyl i musi to byc widoczne"


async def test_swiezosc_liczy_sie_z_potwierdzenia_a_nie_z_probki(db):
    """The same scenario seen through /api/status: the series must stay `live`."""
    t0 = utcnow() - timedelta(minutes=8)
    await ingest_one(db, machine_name="desktop", payload=payload(captured_at=t0))
    await db.commit()
    await ingest_one(db, machine_name="desktop",
                     payload=payload(captured_at=utcnow() - timedelta(seconds=30)))
    await db.commit()

    st = await build_status(db)
    five = {s.series_key: s for s in st.accounts[0].series}["bucket:five_hour"]
    assert five.freshness == "live"
    assert five.utilization is not None


async def test_value_since_nie_przesuwa_sie_przy_niezmienionej_wartosci(db, monkeypatch):
    """"Unchanged since" must point at the start of the constant value. Were a heartbeat
    write to move it, the counter would reset on every heartbeat and show an untruth."""
    monkeypatch.setattr(settings, "sample_heartbeat_sec", 60)
    t0 = _t0(600)
    await send(db, payload(captured_at=t0))
    await db.commit()
    # over the heartbeat => the sample WILL be written even though the value is the same
    t1 = t0 + timedelta(seconds=230)
    await send(db, payload(captured_at=t1))
    await db.commit()

    st = await _five_hour_state(db)
    assert st.last_captured_at == t1, "heartbeat zapisal nowa probke"
    assert st.value_since == t0, "ale wartosc trwa niezmiennie od pierwszego pomiaru"


async def test_value_since_przesuwa_sie_przy_zmianie_wartosci(db):
    t0 = _t0(600)
    await send(db, payload(captured_at=t0))
    await db.commit()
    t1 = t0 + timedelta(seconds=120)
    await send(db, payload(usage=with_util(five_hour=91.0), captured_at=t1))
    await db.commit()

    st = await _five_hour_state(db)
    assert float(st.last_utilization) == 91.0
    assert st.value_since == t1


async def test_zrodlo_pomiaru_trafia_do_probki_i_batcha(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    batch = (await db.execute(select(IngestBatch))).scalars().first()
    assert batch.measurement_source == "cli_merged"
    assert batch.cache_age_s == 42 and batch.fresh_age_s == 7
    sample = (await db.execute(select(LimitSample))).scalars().first()
    assert sample.source == "cli_merged"


async def test_payload_bez_measurement_ladzie_jako_probe(db):
    """Backward compatibility: spool entries written by probe v2 have no measurement block."""
    p = payload()
    del p["measurement"]
    await ingest_one(db, machine_name="desktop", payload=p)
    await db.commit()
    assert (await db.execute(select(LimitSample))).scalars().first().source == "probe"


async def test_brak_token_meta_nie_wywraca_zapisu(db):
    """macOS keeps credentials in the Keychain — since probe version 3 the measurement
    does not need them."""
    p = payload()
    del p["token_meta"]
    r = await ingest_one(db, machine_name="mac", payload=p)
    await db.commit()
    assert r["ok"] and r["samples_written"] > 0
    acc = (await db.execute(select(Account))).scalars().one()
    assert acc.subscription_type is None      # no plan tag, but the data is there


async def test_poprawiona_etykieta_dochodzi_do_serii_zarejestrowanej_wczesniej(db):
    """A label is a DESCRIPTION, not an identity — which is why `get_or_create_series`
    refreshes it on every measurement. Without that, a wording fix would never reach series
    already registered and the UI would show the old text for the rest of the database's
    life. The mechanism had no test, and the `spend:org` label change rests on it."""
    from sqlalchemy import update as sa_update

    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    await db.execute(sa_update(UsageSeries)
                     .where(UsageSeries.series_key == "spend:org")
                     .values(display_label="Label from a previous version"))
    await db.commit()

    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()

    s = (await db.execute(
        select(UsageSeries).where(UsageSeries.series_key == "spend:org")
    )).scalar_one()
    assert s.display_label == "Spend limit (your pool)"
