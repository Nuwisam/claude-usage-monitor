"""POST /api/session-alert i ramka `alert`.

Ten endpoint jest inny niz reszta backendu: nie dotyka bazy, a jego stan zyje w pamieci
procesu. Testy pilnuja wiec czterech rzeczy, ktore w tej konstrukcji psuja sie po cichu:

  * alert opublikowany W TRAKCIE budowania snapshotu musi przezyc (regresja na petli
    kasujacej kolejke w routers/stream.py),
  * kazda ramka niesie PELNY zbior, wiec po `lag` nastepna ramka odtwarza stan,
  * POST zastepuje zbior maszyny w calosci — pusta lista gasi alerty tej maszyny,
  * `machine` bierze sie z TOKENU, nigdy z ciala zadania.
"""
from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

from tests.test_stream import (  # noqa: F401 — fixtury `api`/`db` jada razem z tym
    ACCOUNT_MAX, api, cards, clean_broker, db, ingest_one, listen, parse_sse,
    payload, utcnow, with_util,
)

from app.config import settings
from app.services.events import ALERTS, broker

UUID_MAX = ACCOUNT_MAX["uuid"]

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def clean_alerts():
    ALERTS.clear()
    yield
    ALERTS.clear()


def entry(**kw):
    # `since` MUSI byc liczone teraz, nie wpisane na sztywno: `current_alerts()` odsiewa
    # wpisy starsze niz ALERT_MAX_AGE_SEC (24 h), wiec staly stempel zamienia kazdy test
    # zbioru w bombe zegarowa — przechodzi dobe od napisania i pada na zawsze potem.
    # Test filtra wieku podaje swoja wlasna date i tej domyslnej nie uzywa.
    base = {"key": "sesja__main__abc", "reason": "permission", "project": "proj",
            "tool": "Bash", "detail": "git status",
            "since": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    base.update(kw)
    return base


async def send(api, entries, token="t"):
    return await api.post("/api/session-alert", json={"entries": entries},
                          headers={"Authorization": "Bearer %s" % token})


def alerts_of(events):
    return [d["alerts"] for e, d in events if e == "alert"]


# --------------------------------------------------------------------------- endpoint
async def test_bez_tokenu_nie_wchodzi(api):
    r = await api.post("/api/session-alert", json={"entries": []})
    assert r.status_code == 401


async def test_zly_token_nie_wchodzi(api):
    r = await send(api, [], token="nie-ten")
    assert r.status_code == 401


async def test_nazwa_maszyny_pochodzi_z_tokenu_a_nie_z_ciala(api):
    """Gdyby klient mogl przyslac `machine`, kazda maszyna mogla by podszyc sie pod
    cudze wpisy — a to jest etykieta, ktora czlowiek czyta z panelu i po ktorej
    decyduje, gdzie isc."""
    r = await send(api, [entry(machine="cudza-maszyna")])
    assert r.status_code == 200
    assert r.json()["machine"] == "desktop"
    assert ALERTS["desktop"][0].machine == "desktop"


async def test_zepsuty_wpis_nie_kasuje_calego_zbioru(api):
    """Zgaszenie alertu przez blad formatowania byloby najgorszym trybem awarii tej
    funkcji — to ten sam rodzaj bledu co falszywe zero w pomiarze."""
    r = await send(api, [entry(key="a"), {"reason": "permission"}, entry(key="b")])
    assert r.status_code == 200
    assert r.json()["accepted"] == 2


async def test_pusta_lista_gasi_alerty_maszyny(api):
    await send(api, [entry()])
    assert ALERTS
    await send(api, [])
    assert "desktop" not in ALERTS


async def test_entries_musi_byc_lista(api):
    r = await api.post("/api/session-alert", json={"entries": "nie-lista"},
                       headers={"Authorization": "Bearer t"})
    assert r.status_code == 400


async def test_za_duze_cialo(api, monkeypatch):
    monkeypatch.setattr(settings, "max_ingest_body_bytes", 50)
    r = await send(api, [entry()])
    assert r.status_code == 413


# --------------------------------------------------------------------------- strumien
async def test_snapshot_niesie_alerty_zastane_przed_polaczeniem(api, monkeypatch):
    """Najwazniejszy test w tym pliku.

    STREAM_MAX_LIFETIME_SEC zmusza panel do przelaczenia polaczenia co 15 minut,
    a zablokowana sesja nie emituje w tym czasie ZADNEGO zdarzenia (zmierzone: 98%
    blokad). Bez odtworzenia stanu w snapshocie blokada trwajaca 40 minut znikalaby
    z ekranu po pietnastu — i nikt by nie zauwazyl, ze zniknela.
    """
    await send(api, [entry()])
    events = await listen(api, monkeypatch, query="account=%s" % UUID_MAX)
    ramki = alerts_of(events)
    assert ramki, "snapshot musi zawierac ramke `alert`"
    assert ramki[0][0]["project"] == "proj"
    assert ramki[0][0]["machine"] == "desktop"


async def test_alert_w_trakcie_snapshotu_przezywa(api, monkeypatch):
    """Regresja na `stream.py`: petla po snapshocie kasuje wszystko, co wpadlo do
    kolejki w trakcie jego budowania. Dla ramek `account` jest to nieszkodliwe
    z zalozenia (snapshot je zastepuje), dla efemerycznego alertu byloby ciche
    i smiertelne — dlatego ramka alertu leci PO tej petli, nie przez kolejke."""
    async def work():
        await send(api, [entry(project="wpadl-w-trakcie")])

    events = await listen(api, monkeypatch, query="account=%s" % UUID_MAX, work=work)
    widziane = [a["project"] for ramka in alerts_of(events) for a in ramka]
    assert "wpadl-w-trakcie" in widziane


async def test_kazda_ramka_niesie_pelny_zbior(api, monkeypatch):
    """Nie przyrost. Dlatego zgubienie ramki jest nieszkodliwe, a `lag` wystarcza
    jako jedyny sygnal przerwy."""
    async def work():
        await send(api, [entry(key="a", project="alfa")])
        await send(api, [entry(key="a", project="alfa"),
                         entry(key="b", project="beta")])

    events = await listen(api, monkeypatch, query="account=%s" % UUID_MAX, work=work)
    ostatnia = alerts_of(events)[-1]
    assert sorted(a["project"] for a in ostatnia) == ["alfa", "beta"]


async def test_alert_dociera_niezaleznie_od_subskrypcji_kont(api, monkeypatch):
    """`publish_all`, nie `publish`: alert nie nalezy do zadnego pojedynczego konta.
    Panel zapisany na jedno konto ma zobaczyc blokade w projekcie, ktory akurat chodzi
    na drugim."""
    async def work():
        await send(api, [entry()])

    events = await listen(api, monkeypatch,
                          query="account=uuid-ktorego-nie-ma", work=work)
    assert alerts_of(events), "ramka `alert` nie moze zalezec od tego, na co panel jest zapisany"


async def test_przepelniona_kolejka_daje_lag_a_nastepna_ramka_odtwarza_stan(monkeypatch):
    """Przy przepelnieniu `_drain` wyrzuca WSZYSTKIE zakolejkowane ramki i wstawia
    `lag`. Historia naprawcza dziala tylko dlatego, ze nastepna ramka jest pelnym
    stanem — dla przyrostu ten mechanizm bylby cicha utrata danych.

    Sprawdzane na samym brokerze, nie przez endpoint: przez HTTP odbiorca oproznia
    kolejke tak szybko, jak ta rosnie, wiec przepelnienia nie da sie tam wywolac
    inaczej niz przez sztuczne wstrzymanie generatora — a wtedy testowaloby sie
    wstrzymanie, nie przepelnienie.
    """
    from app.services.events import alert_frame, set_alerts
    from app.schemas import SessionAlert

    monkeypatch.setattr(settings, "stream_queue_max", 2)
    sub = broker.subscribe(frozenset(["dowolne"]), "panel")
    try:
        for i in range(6):
            set_alerts("desktop", [SessionAlert(**entry(key="k%d" % i,
                                                        project="p%d" % i,
                                                        machine="desktop"))])
            broker.publish_all(alert_frame(now=utcnow()))
        ramki = []
        while not sub.queue.empty():
            ramki.append(sub.queue.get_nowait())
    finally:
        broker.unsubscribe(sub)

    assert any("event: lag" in r for r in ramki)
    # Ostatnia ramka po `lag` niesie caly biezacy stan, wiec zguba sie sama leczy.
    assert "p5" in ramki[-1]


async def test_stary_wpis_wypada_ze_snapshotu(api, monkeypatch):
    """Maszyna, ktora zniknela w trakcie blokady, nigdy nie przysle korekty. Writer ma
    wlasny TTL, ale serwer nie moze na nim polegac."""
    from app.services import events as ev

    monkeypatch.setattr(ev, "ALERT_MAX_AGE_SEC", 1.0)
    await send(api, [entry(since="2020-01-01T00:00:00Z")])
    ramki = alerts_of(await listen(api, monkeypatch, query="account=%s" % UUID_MAX))
    assert ramki and ramki[0] == []


async def test_restart_procesu_czysci_mape():
    """Swiadome: alerty wracaja przy najblizszym zdarzeniu z maszyny, a zapis do bazy
    oznaczalby migracje i cykl zycia wierszy dla stanu z definicji chwilowego."""
    ALERTS["desktop"] = ["cokolwiek"]
    ALERTS.clear()                      # to robi start procesu
    assert ALERTS == {}


async def test_przegladarkowy_zestaw_listenerow_nie_widzi_ramki(api, monkeypatch):
    """`useLiveStream.ts` rejestruje piec nazwanych listenerow i NIE MA `onmessage`,
    wiec ramka `alert` jest dla przegladarki niewidzialna z konstrukcji. Ten test
    przypina te wlasnosc do nazwy zdarzenia, zeby zmiana na `message` nie przeszla
    niezauwazona."""
    async def work():
        await send(api, [entry()])

    events = await listen(api, monkeypatch, query="account=%s" % UUID_MAX, work=work)
    znane_przegladarce = {"hello", "ping", "account", "lag", "bye"}
    assert any(e == "alert" for e, _ in events)
    assert "alert" not in znane_przegladarce
