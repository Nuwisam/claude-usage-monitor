"""Granica czasu na WEJSCIU do /api/history.

Regresja na blad z produkcji: widok Historia oddawal 500 przy kazdym otwarciu.

Kontrakt v2 dopial strefe do czasu WYCHODZACEGO — i przez to przegladarka zaczela ja
ODSYLAC, bo `Date.toISOString()` konczy sie na 'Z'. Pydantic robil z tego datetime ze
strefa, a baza, `utcnow()` i probki sa naiwne w UTC. Odejmowanie w `_quiet_intervals`
wywracalo sie na "can't subtract offset-naive and offset-aware datetimes".

Testy jednostkowe tego nie zlapaly, bo wolaja `_find_gaps` wprost, podajac naiwne
datetime'y — czyli omijaja dokladnie ta warstwe, w ktorej blad powstaje. Dlatego te testy
ida przez HTTP: parametr musi przejsc walidacje FastAPI, tak jak z przegladarki.

Drugi, gorszy przypadek pokrywa `test_offset_inny_niz_utc...`: sterownik MySQL formatuje
datetime `strftime`-em i tzinfo IGNORUJE, wiec parametr z '+02:00' nie wywalilby sie —
po cichu przesunalby caly zakres o dwie godziny.
"""
from datetime import timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from tests.test_ingest_e2e import (  # noqa: F401 — fixture `db` przychodzi razem z nimi
    ACCOUNT_MAX, db, ingest_one, payload, utcnow,
)

from app.models import UsageSeries


@pytest_asyncio.fixture
async def api(db):
    """Aplikacja z podmieniona sesja i pominietym SSO. httpx zamiast TestClient, zeby
    dzielic petle zdarzen z fixturem `db` — sesja aiosqlite nalezy do tej petli."""
    import app.main as main
    from app.db import get_session
    from app.sso import CurrentUser, require_authorized_user

    main.app.dependency_overrides[get_session] = lambda: db
    main.app.dependency_overrides[require_authorized_user] = lambda: CurrentUser(
        email="a@b.pl", verified_at=None
    )
    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    main.app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def dane(db):
    """Kilka probek w oknie 5 h. Zakres pytania jest duzo szerszy niz dane, wiec
    powstaje dziura `client_silent` — czyli wlasnie ta sciezka, ktora sie wywracala."""
    now = utcnow()
    for offset in (240, 120, 0):
        await ingest_one(db, machine_name="desktop",
                         payload=payload(captured_at=now - timedelta(seconds=offset)))
    await db.commit()
    sid = (await db.execute(
        select(UsageSeries.id).where(UsageSeries.series_key == "bucket:five_hour")
    )).scalar_one()
    return {"account": ACCOUNT_MAX["uuid"], "seriesId": sid, "now": now}


def _iso(dt, suffix="Z"):
    return dt.isoformat() + suffix


async def test_zakres_ze_strefa_nie_wywraca_endpointu(api, dane):
    """Dokladnie to, co wysyla przegladarka: `Date.toISOString()`, czyli sufiks 'Z'."""
    r = await api.get("/api/history", params={
        "account": dane["account"], "seriesId": dane["seriesId"],
        "from": _iso(dane["now"] - timedelta(hours=2)),
        "to": _iso(dane["now"]),
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["points"], "probki z okna musza sie znalezc w zakresie"
    assert any(g["kind"] == "client_silent" for g in body["gaps"]), \
        "pusty poczatek zakresu to cisza klienta — i to ta sciezka sie wywracala"


async def test_zapis_ze_strefa_i_bez_strefy_daje_ten_sam_wynik(api, dane):
    """Naiwny UTC juz dzialal i ma dzialac dalej — konwersja nie moze zmienic znaczenia."""
    args = {"account": dane["account"], "seriesId": dane["seriesId"]}
    frm, to = dane["now"] - timedelta(hours=2), dane["now"]

    ze = await api.get("/api/history", params={**args, "from": _iso(frm), "to": _iso(to)})
    bez = await api.get("/api/history", params={**args, "from": frm.isoformat(),
                                                "to": to.isoformat()})
    assert ze.status_code == bez.status_code == 200
    assert ze.json() == bez.json()


async def test_offset_inny_niz_utc_jest_przeliczany_a_nie_obcinany(api, dane):
    """Ten sam MOMENT zapisany w innej strefie musi dac ta sama odpowiedz.

    To jest ten cichy przypadek: gdyby offset zostal zignorowany zamiast przeliczony,
    zakres przesunalby sie o dwie godziny, a odpowiedz dalej mialaby HTTP 200 i wygladala
    poprawnie. Zaden objaw, zle dane — czyli tryb awarii, przed ktorym broni sie caly
    ten projekt.
    """
    args = {"account": dane["account"], "seriesId": dane["seriesId"]}
    frm, to = dane["now"] - timedelta(hours=2), dane["now"]

    utc = await api.get("/api/history", params={**args, "from": _iso(frm), "to": _iso(to)})
    # ta sama chwila, zapisana jako czas lokalny
    plus2 = await api.get("/api/history", params={
        **args,
        "from": _iso(frm + timedelta(hours=2), "+02:00"),
        "to": _iso(to + timedelta(hours=2), "+02:00"),
    })
    assert utc.status_code == plus2.status_code == 200, plus2.text
    assert utc.json() == plus2.json()


async def test_brak_zakresu_dalej_dziala(api, dane):
    """Domyslka bierze `utcnow()`, czyli czas NAIWNY — ta sciezka nie moze ucierpiec
    przy okazji naprawy. `bucket=raw` jawnie, bo downsampling stoi na funkcjach
    MariaDB (`unix_timestamp`, `group_concat ... ORDER BY`), ktorych SQLite nie ma."""
    r = await api.get("/api/history", params={
        "account": dane["account"], "seriesId": dane["seriesId"], "bucket": "raw",
    })
    assert r.status_code == 200, r.text
    assert r.json()["points"]
