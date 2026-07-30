"""Backlog, granice transakcji i idempotencja — to, co da sie sprawdzic bez MariaDB.

CZEGO TEN PLIK NIE POKRYWA, swiadomie i wprost:
  * samego bledu 1020 — jest specyficzny dla MariaDB z innodb_snapshot_isolation=ON,
    a suite chodzi na SQLite;
  * PRAWDZIWEJ rownoleglosci — kazdy test ponizej jest SEKWENCYJNY. Ostatni pokazuje
    SKUTEK przeplotu (cudzy commit w szczelinie), nie sam przeplot;
  * utraty odpowiedzi po stronie sondy — to jest po drugiej stronie HTTP.

Uwaga na kontrakt: `IngestResult` to `CamelModel`, a FastAPI serializuje po aliasie, wiec
na drucie jest `backlogAccepted`, nie `backlog_accepted`. Sonda czyta to samo
(client/usage-probe.py, `parsed.get("backlogAccepted")`).
"""
import asyncio
from datetime import timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base, IngestBatch, LimitSample, Machine
from app.routers import ingest as ingest_router
from app.routers.ingest import _ingest_tx
from app.services.ingest import ingest_one
from tests.test_ingest_e2e import db, payload, utcnow, with_util   # noqa: F401

# Wpis, ktorego `ingest_one` nie przetrawi: `payload.get("account") or {}` przepuszcza
# liczbe (jest prawdziwa), a `acct.get("uuid")` na niej rzuca AttributeError. Bez
# monkeypatcha, bo to jest realny ksztalt "trwale zlego wpisu".
ZLY_WPIS = {"account": 123}


@pytest_asyncio.fixture(autouse=True)
async def _swieza_blokada(monkeypatch):
    """`asyncio.Lock` na poziomie modulu wiaze sie z petla zdarzen przy pierwszym
    KONFLIKCIE (od 3.10 `acquire()` ma bezkolizyjna szybka sciezke przed `_get_loop()`),
    a pytest-asyncio daje kazdemu testowi WLASNA petle. W produkcji proces ma jedna petle
    na cale zycie, wiec lock modulowy jest tam poprawny; konfliktu moze dzis dorobic sie
    tylko ten plik, wiec fixture siedzi TUTAJ, a nie w globalnym conftest.py, ktorego to
    repo nie ma. Gdyby konfliktowal jeszcze jakis inny plik testowy, to wlasnie ta fixture
    nalezy wtedy przeniesc do conftest.py."""
    monkeypatch.setattr(ingest_router, "_WRITE_LOCK", asyncio.Lock())


@pytest_asyncio.fixture
async def api(db, monkeypatch):
    """Prawdziwy endpoint, bo petla backlogu zyje w routerze, nie w `ingest_one`.
    Bez latania SSO — `/api/ingest` wisi tylko na `require_ingest_token`."""
    import app.main as main
    from app.db import get_session

    monkeypatch.setattr(settings, "ingest_tokens_raw", "t:desktop")
    main.app.dependency_overrides[get_session] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=main.app),
                           base_url="http://test") as c:
        yield c
    main.app.dependency_overrides.clear()


async def post(api, body):
    return await api.post("/api/ingest", json=body,
                          headers={"Authorization": "Bearer t"})


async def ile(db, model) -> int:
    return (await db.execute(select(func.count()).select_from(model))).scalar_one()


async def probki_o(db, ts) -> int:
    """Probki dokladnie na tym captured_at. Goly licznik calej tabeli tu nie wystarczy:
    kazde zadanie dopisuje jeszcze pomiar BIEZACY, wiec suma rosnie tak czy tak."""
    return (await db.execute(
        select(func.count()).select_from(LimitSample)
        .where(LimitSample.captured_at == ts)
    )).scalar_one()


# ------------------------------------------------------- ksiegowanie `backlogAccepted`
async def test_wpis_niebedacy_obiektem_jest_liczony(api, db):
    """Prefiks, nie licznik sukcesow: bez inkrementu sonda obcielaby zly kawalek spoola."""
    now = utcnow()
    p = payload(captured_at=now)
    p["backlog"] = ["nie-obiekt", payload(captured_at=now - timedelta(minutes=5))]
    r = await post(api, p)
    assert r.status_code == 200
    # 2, nie 1: pozycji 0 nie da sie zapisac nigdy, wiec przyznajemy sie do niej.
    assert r.json()["backlogAccepted"] == 2


async def test_stop_na_pierwszym_niezapisanym_wpisie(api, db):
    """`break`, nie `continue` — inaczej `accepted` obejmuje wpis, ktorego nie zapisalismy."""
    now = utcnow()
    p = payload(captured_at=now)
    p["backlog"] = [
        payload(captured_at=now - timedelta(minutes=9)),   # 0 — przejdzie
        ZLY_WPIS,                                          # 1 — padnie
        payload(captured_at=now - timedelta(minutes=3)),   # 2 — NIE moze byc policzony
    ]
    r = await post(api, p)
    assert r.status_code == 200
    assert r.json()["backlogAccepted"] == 1
    # Zywy pomiar + wpis 0. Wpis 1 dolecial do `db.add(batch)` i `flush()`, ale rollback
    # jego transakcji go zabral; wpisu 2 nie tkneliesmy. Ogon wroci nastepnym zadaniem.
    assert await ile(db, IngestBatch) == 2


async def test_blad_w_backlogu_nie_cofa_pomiaru_zywego(api, db):
    """Wlasna transakcja per wpis. Kluczowa jest tu asercja na LICZBIE batchy: bez
    rollbacku wiersz batcha zlego wpisu zostaje w sesji i wspolny `commit()` na koncu
    zadania zapisuje go razem z resztą — czyli w bazie ląduje batch pomiaru, ktorego
    nigdy nie bylo. Same `ok`/`backlogAccepted`/liczba probek tego nie rozroznia."""
    p = payload()
    p["backlog"] = [ZLY_WPIS]
    r = await post(api, p)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert r.json()["backlogAccepted"] == 0
    assert await ile(db, LimitSample) > 0
    # 1, nie 2: batch zlego wpisu zostal cofniety razem z jego transakcja.
    assert await ile(db, IngestBatch) == 1


# --------------------------------------------------------------------- idempotencja
async def test_powtorka_ze_spoola_nie_dubluje_probki(api, db):
    """Odpowiedz przepadla, sonda nie obciela spoola, ten sam wpis przychodzi drugi raz.

    Wartosc wpisu MUSI sie roznic od pomiaru biezacego, inaczej dedup wyrzuca go w calosci
    (to jest osobny finding F5) i test przechodzilby prozno — dlatego `with_util` i
    dlatego asercja `po_pierwszym > 0`."""
    now = utcnow().replace(microsecond=0)
    ts = now - timedelta(minutes=20)
    stary = payload(usage=with_util(five_hour=0.11), captured_at=ts)

    p1 = payload(captured_at=now)
    p1["backlog"] = [stary]
    assert (await post(api, p1)).status_code == 200
    po_pierwszym = await probki_o(db, ts)
    assert po_pierwszym > 0, "wpis z backlogu nie zapisal nic — test nie sprawdza niczego"

    # Ta sama tresc wpisu, nowy pomiar biezacy — dokladnie to, co robi sonda po timeoucie.
    p2 = payload(captured_at=now + timedelta(minutes=2))
    p2["backlog"] = [stary]
    r = await post(api, p2)
    assert r.status_code == 200
    # Wpis MUSI byc policzony, zeby sonda w koncu obciela spool...
    assert r.json()["backlogAccepted"] == 1
    # ...ale nie moze dopisac drugiego wiersza na tym samym captured_at.
    assert await probki_o(db, ts) == po_pierwszym


async def test_dwa_rozne_pomiary_w_tej_samej_sekundzie_przechodza(api, db):
    """`parse_ts` obcina do pelnych sekund, a rownolegle hooki potrafia odpalic dwie sondy
    w tej samej sekundzie. Guard nie ma prawa skasowac drugiego, ROZNEGO pomiaru — dlatego
    w kluczu jest sha256 payloadu, nie tylko czas. Bez tego warunku wychodzi tu 1."""
    now = utcnow().replace(microsecond=0)
    a = payload(usage=with_util(five_hour=0.11), captured_at=now)
    b = payload(usage=with_util(five_hour=0.44), captured_at=now)

    p = payload(captured_at=now + timedelta(minutes=1))
    p["backlog"] = [a, b]
    r = await post(api, p)
    assert r.status_code == 200
    assert r.json()["backlogAccepted"] == 2
    assert await probki_o(db, now) >= 2


# ------------------------------------------------------------- niezmiennik identity map
async def test_expire_all_zdejmuje_nieswiezy_obiekt_z_identity_map(tmp_path):
    """Cudzy commit w szczelinie, gdy lock jest oddany miedzy transakcjami tego samego
    zadania. Bez `db.expire_all()` w `_ingest_tx` sesja A trzyma obiekt `Machine` z
    `batches=1`, nie widzi inkrementu sesji B i nadpisuje go swoim `1 + 1` — ostatnia
    asercja pokazuje wtedy 2 zamiast 3. Zgubiony inkrement, zero bledu.

    DWIE rzeczy w konstrukcji sa krytyczne i kazda z nich juz raz zepsula ten test:

    1. Referencje `trzymana` bierzemy WEWNATRZ `_ingest_tx`. Gdyby `select()` stal poza
       nim, autobegin otwarlby na sesji A transakcje, ktorej nikt nie zamyka — snapshot
       zostalby przypiety sprzed commitu B, a przy okazji ten odczyt odswiezylby obiekt,
       ktory `expire_all()` wlasnie wygasil. Test padalby wtedy ZAWSZE, niezaleznie od
       latki. Wartosc czytamy do zwyklego inta jeszcze przed wyjsciem z bloku, bo po
       `expire_all()` dostep do atrybutu poszedlby po bazie.
    2. Referencje trzymamy w ogole, bo produkcja tego nie robi: `ingest_one` zwraca same
       skalary, wiec obiekt zwykle ginie razem z ramka i weak identity map go wyrzuca.
       Wlasnie dlatego ta linia jest w latce — poprawnosc zalezna od tego, czy refcounting
       zdazyl posprzatac, jest niezapisanym nigdzie warunkiem, ktorego nic nie chroni.

    Baza jest PLIKOWA, zeby dwie sesje mialy dwa OSOBNE polaczenia: dla `:memory:`
    SQLAlchemy bierze StaticPool i obie sesje siedza na jednym polaczeniu, wiec "commit
    sesji B" zatwierdzalby tez prace sesji A. Sam mechanizm identity map dalby sie pokazac
    i tam, ale modelowalby wtedy co innego niz cudze zadanie."""
    url = "sqlite+aiosqlite:///%s" % (tmp_path / "ingest.db").as_posix()
    silnik = create_async_engine(url)
    async with silnik.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    fabryka = async_sessionmaker(silnik, expire_on_commit=False)   # jak produkcja

    now = utcnow().replace(microsecond=0)
    try:
        async with fabryka() as a, fabryka() as b:
            async with _ingest_tx(a):
                await ingest_one(a, machine_name="desktop", payload=payload(captured_at=now))
                trzymana = (await a.execute(select(Machine))).scalars().one()
                po_pierwszym = trzymana.batches
            assert po_pierwszym == 1

            # Sesja B — inne polaczenie, wlasna transakcja, wlasny identity map.
            async with _ingest_tx(b):
                await ingest_one(b, machine_name="desktop",
                                 payload=payload(captured_at=now + timedelta(seconds=1)))

            # Sesja A wraca do tego samego wiersza, tak jak przy drugim wpisie backlogu.
            async with _ingest_tx(a):
                await ingest_one(a, machine_name="desktop",
                                 payload=payload(captured_at=now + timedelta(seconds=2)))
            assert trzymana is not None      # referencja musi dozyc do konca

        async with fabryka() as c:
            batches = (await c.execute(select(Machine.batches))).scalar_one()
        assert batches == 3, "inkrement z cudzego commitu zostal nadpisany"
    finally:
        await silnik.dispose()
