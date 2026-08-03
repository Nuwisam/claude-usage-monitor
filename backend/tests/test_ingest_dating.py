"""Datowanie pomiaru: dwa zrodla o roznym wieku, jedna kotwica po stronie serwera.

Pomiar sondy skleja wartosci z dwoch miejsc — zrzutu `claude -p "/usage"` (same procenty
glownych okien) i cache'u Claude Code (wszystko, w tym `spend` i `extra_usage`). Zrodla maja
ROZNY WIEK: zrzut wolno miec do 900 s, cache do 3600 s. Dopoki oba jechaly na jednym stemplu,
wzietym ze zrzutu, `spend` i `extra_usage` byly odmladzane o cala te roznice.

To nie jest kosmetyka. Backend rozstrzyga po tym stemplu, ktory odczyt jest BIEZACY
(`newest`, services/ingest.py), a guard monotonicznosci wymaga znanej granicy okna po obu
stronach — ktorej te dwie serie nie maja NIGDY. Sa wiec jedynymi dwiema seriami bez zadnej
obrony, i akurat one sa wiazacym limitem na koncie Team.

Wystarcza DWIE maszyny i idealnie zsynchronizowane zegary.
"""
import copy
import importlib.util
from datetime import timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import IngestBatch, IngestEvent, LimitSample, SeriesState, UsageSeries
from app.parsing import parse_usage, probe_key
from app.services.ingest import ingest_one, measured_at, request_offset, utcnow
from tests.team import ACCOUNT_TEAM_REAL, USAGE_ACTIVE, usage
from tests.test_ingest_e2e import count, db, payload, send   # noqa: F401

PROBE = Path(__file__).resolve().parents[2] / "client" / "usage-probe.py"


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("usage_probe", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def team(spend_percent=None, five_hour=None):
    u = usage(USAGE_ACTIVE)
    if spend_percent is not None:
        u["spend"]["percent"] = spend_percent
    if five_hour is not None:
        u["five_hour"]["utilization"] = five_hour
    return u


async def _state(db, series_key):
    return (await db.execute(
        select(SeriesState).join(UsageSeries, UsageSeries.id == SeriesState.series_id)
        .where(UsageSeries.series_key == series_key)
    )).scalars().first()


# ------------------------------------------------------------------- regresja glowna
async def test_starszy_cache_nie_cofa_spend_mimo_swiezszego_zrzutu(db):
    """SEDNO CALEJ ZMIANY.

    Maszyna A: cache 3 min temu (spend 93%), zrzut 1 min temu.
    Maszyna B: cache 4 min temu (spend 92%), zrzut 30 s temu.

    B ma swiezszy ZRZUT, wiec jej okna sa nowsze i maja wygrac. Ale jej CACHE jest starszy,
    wiec `spend` ma zostac po A. Przed ta zmiana jeden stempel na caly pomiar oddawal B
    wszystko i stan cofal sie na odczyt starszy o minute — a przy realnych granicach wiekow
    obu zrodel siegalo to godziny.
    """
    t = utcnow().replace(microsecond=0)

    await send(db, payload(account=ACCOUNT_TEAM_REAL, usage=team(spend_percent=93, five_hour=50.0),
                           captured_at=t - timedelta(seconds=180),
                           fresh_at=t - timedelta(seconds=60),
                           fresh_covered=["bucket:five_hour", "bucket:seven_day"],
                           sent_at=t), machine="A", arrived_at=t)
    await db.commit()

    await send(db, payload(account=ACCOUNT_TEAM_REAL, usage=team(spend_percent=92, five_hour=51.0),
                           captured_at=t - timedelta(seconds=240),
                           fresh_at=t - timedelta(seconds=30),
                           fresh_covered=["bucket:five_hour", "bucket:seven_day"],
                           sent_at=t), machine="B", arrived_at=t)
    await db.commit()

    spend = await _state(db, "spend:org")
    assert float(spend.last_utilization) == 93.0, "starszy cache nie moze cofnac spend"
    assert spend.last_captured_at == t - timedelta(seconds=180)

    five = await _state(db, "bucket:five_hour")
    assert float(five.last_utilization) == 51.0, "swiezszy zrzut MA wygrac dla okna"
    assert five.last_captured_at == t - timedelta(seconds=30)

    # Historia nie traci nic — probka B na `spend` jest w bazie, tylko nie rzadzi stanem.
    stamps = {s.captured_at for s in (await db.execute(
        select(LimitSample).where(LimitSample.series_id == spend.series_id)
    )).scalars()}
    assert t - timedelta(seconds=240) in stamps


async def test_bez_fresh_covered_wszystko_idzie_po_cache(db):
    """Lagodna degradacja, nie galaz kompatybilnosci: payload bez `fresh_covered` datuje
    sie w calosci po `captured_at`, czyli po cache. Tak przechodza wpisy w spoolu zapisane
    przez sonde ponizej v5."""
    t = utcnow().replace(microsecond=0)
    await send(db, payload(account=ACCOUNT_TEAM_REAL, usage=team(),
                           captured_at=t - timedelta(seconds=300),
                           fresh_at=t - timedelta(seconds=10), sent_at=t),
               arrived_at=t)
    await db.commit()
    stamps = {s.captured_at for s in (await db.execute(select(LimitSample))).scalars()}
    assert stamps == {t - timedelta(seconds=300)}


@pytest.mark.parametrize("smiec", [None, 7, "bucket:five_hour", {"a": 1}, [1, None, {}]])
async def test_fresh_covered_zlego_typu_nie_wywraca_zapisu(db, smiec):
    """`frozenset(None)` rzuca TypeError, a `ingest_one` nie ma try/except mimo obietnicy
    w docstringu: dla rekordu biezacego to 500 na cale zadanie, dla wpisu backlogu `break`
    i TRWALE zatkany ogon spoola. Endpoint jest wystawiony w internecie."""
    p = payload(account=ACCOUNT_TEAM_REAL, usage=team())
    p["measurement"]["fresh_covered"] = smiec
    r = await send(db, p)
    await db.commit()
    assert r["ok"] and r["samples_written"] > 0


# ------------------------------------------------------- zgodnosc kluczy sonda <-> backend
def test_probe_key_zgadza_sie_z_kluczami_sondy(probe):
    """Rozjazd tutaj jest CICHY: zbior nigdy sie nie dopasuje, `covered_by_fresh` nigdy sie
    nie zapali i datowanie po cichu cofnie sie do stanu sprzed tej zmiany. Fixture ma limit
    `weekly_scoped` z modelem "Fable" — czyli dokladnie ten przypadek, w ktorym slugowanie
    (`limit_series_key` robi z tego `fable`) rozjechaloby oba konce."""
    u = usage(USAGE_ACTIVE)
    _, covered = probe.merge(copy.deepcopy(u),
                             {"session": 48, "weekly_all": 47, "scoped": {"Fable": 3}})
    assert covered, "fixture musi cokolwiek pokrywac, inaczej test nie sprawdza niczego"

    klucze = {probe_key(o) for o in parse_usage(u).observations}
    assert set(covered) <= klucze


def test_probe_key_nie_sluguje_i_nie_zna_powierzchni():
    """Trzy wlasciwosci, ktorych zlamanie nic nie wywroci, tylko wylaczy mechanizm."""
    u = {"limits": [{"kind": "weekly_scoped", "group": "weekly", "percent": 3,
                     "scope": {"model": {"display_name": "Fable"},
                               "surface": {"display_name": "Cowork"}}}],
         "spend": {"percent": 93, "enabled": True}}
    by_source = {o.source: probe_key(o) for o in parse_usage(u).observations}
    assert by_source["limit"] == "limit:weekly_scoped:Fable"      # surowa nazwa, bez slugu
    assert by_source["spend"] is None                             # zrzut tego nie zna nigdy


def test_probe_key_dla_limitu_bez_modelu():
    u = {"limits": [{"kind": "session", "group": "session", "percent": 48, "scope": None}]}
    o = parse_usage(u).observations[0]
    assert probe_key(o) == "limit:session:-"


# --------------------------------------------------------------------------- kotwica
def test_measured_at_jest_czysta_funkcja():
    t = utcnow().replace(microsecond=0)
    # Zegar klienta spozniony o godzine, pomiar 120 s stary w JEGO zegarze.
    zegar = t - timedelta(hours=1)
    assert measured_at(zegar - timedelta(seconds=120), t - zegar, t) == t - timedelta(seconds=120)
    # Przyciecie: pomiar nie moze byc nowszy niz chwila odebrania.
    assert measured_at(t + timedelta(seconds=10), timedelta(0), t) == t
    # Brak czasu to niewiedza, nie "teraz".
    assert measured_at(None, timedelta(0), t) is None


async def test_kotwica_jest_wspolna_dla_calego_zadania(db):
    """Kotwice zdejmuje handler PRZED lockiem zapisu. Liczona wewnatrz `ingest_one` — czyli
    juz pod lockiem — dawalaby zadaniu, ktore przeczekalo cudzy backlog, stempel o czas
    czekania za swiezy, a kazdy wpis backlogu wlasna, inna kotwice."""
    arrived = utcnow().replace(microsecond=0) - timedelta(seconds=30)
    p = payload(account=ACCOUNT_TEAM_REAL, usage=team(),
                captured_at=arrived, sent_at=arrived)
    await ingest_one(db, machine_name="desktop", payload=p, arrived_at=arrived,
                     offset=request_offset(p, arrived))
    await db.commit()
    b = (await db.execute(select(IngestBatch))).scalars().one()
    assert b.received_at == arrived
    assert b.clock_offset_s == 0


# ------------------------------------------------------------------- stary backlog
async def test_wpis_sprzed_osmiu_dni_zostaje_stary(db):
    """`BACKLOG_MAX_AGE_SEC` podstawialo pod taki pomiar czas serwera — czyli robilo
    odwrotnosc ochrony: wpis stawal sie najnowszy i przejmowal stan biezacy."""
    t = utcnow().replace(microsecond=0)
    dawno = t - timedelta(days=8)

    await send(db, payload(account=ACCOUNT_TEAM_REAL, usage=team(spend_percent=93),
                           captured_at=t - timedelta(seconds=60), sent_at=t), arrived_at=t)
    await db.commit()

    # `offset` jest WSPOLNY dla zadania i pochodzi z rekordu zewnetrznego, nie z wpisu:
    # to rekord biezacy zostal wyslany teraz. Wlasne `sent_at` wpisu sluzy tylko kontroli
    # "pomiar nie powstal po wysylce".
    p = payload(account=ACCOUNT_TEAM_REAL, usage=team(spend_percent=10),
                captured_at=dawno, sent_at=dawno)
    await ingest_one(db, machine_name="desktop", payload=p, arrived_at=t,
                     offset=timedelta(0), is_backlog=True)
    await db.commit()

    stamps = {s.captured_at for s in (await db.execute(select(LimitSample))).scalars()}
    assert dawno in stamps, "stary pomiar ma zostac zapisany ze swoja data"
    spend = await _state(db, "spend:org")
    assert float(spend.last_utilization) == 93.0, "stary wpis nie moze przejac stanu"


async def test_wpis_z_cofnietym_zegarem_jest_odrzucany_ale_policzony(db):
    """Pomiar nie mogl powstac po wysylce. Odrzucamy calosc — inaczej wyladowalby na
    kotwicy zadania, przeszedl `newest` i nadpisal stan starym odczytem."""
    t = utcnow().replace(microsecond=0)
    p = payload(account=ACCOUNT_TEAM_REAL, usage=team(spend_percent=10),
                captured_at=t - timedelta(seconds=60),
                sent_at=t - timedelta(seconds=600))
    r = await ingest_one(db, machine_name="desktop", payload=p, arrived_at=t,
                         offset=timedelta(0), is_backlog=True)
    await db.commit()

    assert not r["ok"] and r["samples_written"] == 0
    assert await count(db, LimitSample) == 0
    types = {e.event_type for e in (await db.execute(select(IngestEvent))).scalars()}
    assert "clock_backwards" in types
    b = (await db.execute(select(IngestBatch))).scalars().one()
    assert b.raw_payload_id is not None, "surowy payload i tak idzie do bazy (zasada 6)"


async def test_wpis_bez_sent_at_przechodzi_mimo_nowszej_daty(db):
    """Wpis w spoolu zapisany przez sonde v4 niesie w `captured_at` czas ZRZUTU, rutynowo
    NOWSZY niz `captured_at` rekordu biezacego (ktory od v5 jest czasem cache'u). Kryterium
    odrzucania stoi na `sent_at` wlasnie po to, zeby takich wpisow nie skasowac."""
    t = utcnow().replace(microsecond=0)
    p = payload(account=ACCOUNT_TEAM_REAL, usage=team(spend_percent=88),
                captured_at=t - timedelta(seconds=10))
    r = await ingest_one(db, machine_name="desktop", payload=p, arrived_at=t,
                         offset=timedelta(0), is_backlog=True)
    await db.commit()
    assert r["ok"] and r["samples_written"] > 0
