"""Kontrakt /api/status v2 — to, na czym stoi UI.

Pilnuje trzech rzeczy, ktorych zlamanie jest niewidoczne w kodzie backendu i wychodzi
dopiero na ekranie:
  1. kazdy znacznik czasu na drucie ma strefe (bez tego przegladarka MILCZACO czyta UTC
     jako czas lokalny i countdown jest przesuniety),
  2. serie niosa swoja semantyke (`kind`/`group`/`bucketKey`), zeby UI nie zgadywalo,
     ktora seria jest oknem 5 h,
  3. `unknown` nigdy nie zamienia sie w zero.
"""
import json
import re
from datetime import timedelta

from sqlalchemy import select
from tests.test_ingest_e2e import (  # noqa: F401 — fixture `db` przychodzi razem z nimi
    ACCOUNT_MAX, ACCOUNT_TEAM, REAL, db, ingest_one, payload, utcnow, with_util,
)

from app.models import Account
from app.services.status import CONTRACT_VERSION, build_status

# ISO-8601 z offsetem: "2026-07-26T19:07:37.564772Z" albo "...+00:00"
ISO_TZ = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
DATE_KEYS = ("At", "Now", "resetsAt", "From")


def walk_timestamps(node, path=""):
    """Kazde pole, ktorego nazwa wyglada na date, razem ze sciezka do komunikatu bledu."""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str) and (k.endswith(DATE_KEYS) or k in ("from", "to", "t")):
                yield "%s.%s" % (path, k), v
            else:
                yield from walk_timestamps(v, "%s.%s" % (path, k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk_timestamps(v, "%s[%d]" % (path, i))


async def wire(db):
    """Odpowiedz taka, jak zobaczy ja przegladarka: aliasy camelCase, JSON."""
    st = await build_status(db)
    return json.loads(st.model_dump_json(by_alias=True))


async def test_kazdy_znacznik_czasu_ma_strefe(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    body = await wire(db)

    found = list(walk_timestamps(body))
    assert found, "odpowiedz nie zawiera zadnego znacznika czasu — test bylby pusty"
    for path, value in found:
        assert ISO_TZ.match(value), "%s = %r nie ma strefy" % (path, value)


async def test_contract_version_jest_zadeklarowana_wersja(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    assert (await wire(db))["contractVersion"] == CONTRACT_VERSION == 3


async def test_serie_niosa_semantyke_a_nie_tylko_klucz(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    series = (await wire(db))["accounts"][0]["series"]

    for s in series:
        assert {"kind", "group", "bucketKey"} <= set(s)
    # okno 5 h da sie wskazac bez patrzenia w string klucza i bez sort_order
    sesje = [s for s in series if s["kind"] == "session" or s["bucketKey"] == "five_hour"]
    assert sesje


async def test_kaskada_jest_przy_koncie_i_ma_cztery_szczeble(db):
    await ingest_one(db, machine_name="desktop", payload=payload())
    await db.commit()
    cascade = (await wire(db))["accounts"][0]["cascade"]

    assert [r["key"] for r in cascade] == ["session", "weekly", "credits", "hard_block"]
    assert sum(1 for r in cascade if r["isCurrent"]) == 1


async def test_karte_konta_sklada_dokladnie_jedna_funkcja(db, monkeypatch):
    """Dowod, ze `/api/status` i publikator SSE nie maja jak sie rozjechac.

    Karta konta powstaje w `build_account_status`; `build_status` jest tylko petla po
    kontach. Gdyby ktos kiedys zduplikowal skladanie karty dla strumienia — a to jest
    naturalna pokusa, bo publikator zna juz konto i "wystarczy przepisac" — ten test
    natychmiast pokaze rozjazd.

    `utcnow` przybijamy, bo trzy pola zaleza od chwili odczytu (`freshness`,
    `secondsToReset`, `deltaPct1h`) i bez tego porownanie lapaloby roznice sekundy
    zamiast roznicy logiki.
    """
    import app.services.status as stat

    await ingest_one(db, machine_name="desktop", payload=payload(account=ACCOUNT_MAX))
    await db.commit()
    await ingest_one(db, machine_name="laptop", payload=payload(account=ACCOUNT_TEAM))
    await db.commit()

    teraz = utcnow()
    monkeypatch.setattr(stat, "utcnow", lambda: teraz)

    zbiorczo = json.loads((await build_status(db)).model_dump_json(by_alias=True))
    assert len(zbiorczo["accounts"]) == 2, "scenariusz mial dac dwa konta"

    for karta in zbiorczo["accounts"]:
        acc = (await db.execute(
            select(Account).where(Account.account_uuid == karta["uuid"])
        )).scalar_one()
        pojedynczo, ostrzezenia = await stat.build_account_status(
            db, acc, now=teraz, last_batch_at=await stat.last_batch_time(db, acc.id),
        )
        assert json.loads(pojedynczo.model_dump_json(by_alias=True)) == karta, \
            "karta konta %s rozni sie miedzy sciezkami" % karta["uuid"]
        assert set(ostrzezenia) <= set(zbiorczo["warnings"])


async def test_last_batch_time_zgadza_sie_z_wariantem_grupowym(db):
    """Publikator liczy `last_batch_at` innym zapytaniem niz `/api/status` (jedno konto
    zamiast GROUP BY). To jedyny fakt, ktory ma dwa zrodla — wiec ma tu swoj test."""
    await ingest_one(db, machine_name="desktop", payload=payload(account=ACCOUNT_MAX))
    await ingest_one(db, machine_name="laptop", payload=payload(account=ACCOUNT_TEAM))
    await db.commit()

    import app.services.status as stat
    grupowo = await stat._last_batch_times(db)
    for acc in (await db.execute(select(Account))).scalars().all():
        assert await stat.last_batch_time(db, acc.id) == grupowo.get(acc.id)


async def test_unknown_nie_ma_liczby_ale_ma_ostatni_pomiar(db, monkeypatch):
    """Ten sam warunek, ktory pilnuje test_dzialajacy_klient_bez_probek_daje_unknown —
    tu sprawdzany na DRUCIE, bo to UI dostaje `null` i musi go dostac naprawde.

    Scenariusz: pomiar 42%, okno resetuje sie godzine pozniej, a po resecie przychodza
    batche BEZ ani jednej probki. Klient zyje, wiec to nie bezczynnosc — to awaria,
    i jedyna poprawna odpowiedz jest "nie wiem", nigdy "0%".
    """
    import app.services.ingest as ing
    import app.services.status as stat

    t0 = utcnow()
    u = with_util(five_hour=42.0)
    u["five_hour"]["resets_at"] = (t0 + timedelta(hours=1)).isoformat() + "Z"
    await ingest_one(db, machine_name="desktop", payload=payload(usage=u))

    # batch PO resecie okna, ale bez zadnej serii w payloadzie
    monkeypatch.setattr(ing, "utcnow", lambda: t0 + timedelta(hours=2))
    await ingest_one(db, machine_name="desktop", payload=payload(usage={}))
    await db.commit()

    monkeypatch.setattr(stat, "utcnow", lambda: t0 + timedelta(hours=2, minutes=5))
    series = (await wire(db))["accounts"][0]["series"]

    unknowns = [s for s in series if s["freshness"] == "unknown"]
    assert unknowns, "scenariusz nie wyprodukowal stanu unknown — test bylby pusty"
    for s in unknowns:
        assert s["utilization"] is None, "unknown wyrenderowane jako liczba"
        assert s["rawUtilization"] is not None, "zgubiony ostatni pomiar"


# ------------------------------------------------- wycofany miernik na drucie
async def test_wycofany_miernik_zostawia_ostatni_zmierzony_procent_i_kwoty(db):
    """Fantomem jest `percent: 0` Z PAYLOADU WYCOFANIA, a nie wartosc sprzed blokady.
    Ta druga jest pomiarem i zostaje na ekranie — inaczej zamkniecie bramy KASUJE jedyna
    liczbe, jaka o zuzyciu mamy, czyli pogarsza widok zamiast go naprawiac.

    `utilization` (liczba BIEZACA) znika, bo biezacej nie ma. `rawUtilization` (ostatnia
    ZMIERZONA) zostaje — dokladnie tak, jak przy `unknown`, zasada 4."""
    from tests.team import USAGE_ACTIVE, USAGE_WITHDRAWN, team_payload

    now = (utcnow() - timedelta(seconds=120)).replace(microsecond=0)
    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_ACTIVE, captured_at=now))
    await ingest_one(db, machine_name="desktop",
                     payload=team_payload(USAGE_WITHDRAWN,
                                          captured_at=now + timedelta(seconds=60)))
    await db.commit()

    body = await wire(db)
    spend = {s["seriesKey"]: s for s in body["accounts"][0]["series"]}["spend:org"]
    assert spend["utilization"] is None, "biezacej liczby nie ma i nie wolno jej udawac"
    assert spend["rawUtilization"] == 93.0, "ostatni ZMIERZONY procent zostaje"
    assert spend["unavailableReason"] == "org_level_disabled_until"

    # Kwoty tez pochodza z ostatniego pomiaru — payload wycofania ma `used` wyzerowane
    # i `limit: null`, wiec wziete z niego pokazywalyby 0,00 EUR jako fakt.
    assert spend["extra"]["used"]["amount_minor"] == 27795
    assert spend["extra"]["limit"]["amount_minor"] == 30000

    # Znaczniki opisuja TE liczbe, nie moment stwierdzenia jej braku.
    assert spend["confirmedAt"].startswith(now.isoformat()[:19])

    credits = {r["key"]: r for r in body["accounts"][0]["cascade"]}["credits"]
    assert credits["state"] == "off" and credits["reason"] == "org_level_disabled_until"
    assert (credits["usedMinor"], credits["limitMinor"]) == (27795, 30000)


async def test_seria_z_powodem_nie_ma_biezacej_liczby(db):
    """Implikacja na CALEJ odpowiedzi, nie na jednej serii: powod wyklucza liczbe BIEZACA.
    `utilization` obok powodu byloby twierdzeniem, ze zmierzono wlasnie to, czego zmierzyc
    sie nie da. `rawUtilization` wyklucza sie z nim nie — to pomiar sprzed blokady."""
    from tests.team import USAGE_WITHDRAWN, team_payload

    await ingest_one(db, machine_name="desktop", payload=payload())           # Max
    await ingest_one(db, machine_name="laptop", payload=team_payload(USAGE_WITHDRAWN))
    await db.commit()

    body = await wire(db)
    wszystkie = [s for a in body["accounts"] for s in a["series"]]
    assert wszystkie, "brak serii — test bylby pusty"
    for s in wszystkie:
        if s["unavailableReason"] is not None:
            assert s["utilization"] is None, s["seriesKey"]

    for a in body["accounts"]:
        for r in a["cascade"]:
            if r["reason"] is not None:
                assert r["state"] != "on" or r["key"] == "hard_block"


async def test_contract_version_zostaje_na_trzy_mimo_nowych_pol(db):
    """Dodanie pola nie lamie zgodnosci — precedens `deltaFrom`. Konsument, ktory
    `unavailableReason` nie zna, dostaje dokladnie to samo, co dostawal."""
    from tests.team import USAGE_WITHDRAWN, team_payload

    await ingest_one(db, machine_name="desktop", payload=team_payload(USAGE_WITHDRAWN))
    await db.commit()
    body = await wire(db)

    assert body["contractVersion"] == CONTRACT_VERSION == 3
    assert all("unavailableReason" in s
               for a in body["accounts"] for s in a["series"])
    assert all("reason" in r for a in body["accounts"] for r in a["cascade"])


async def test_fantomowe_zero_zapisane_przed_ta_zmiana_nie_wraca_na_ekran(db):
    """Stan zapisany przez POPRZEDNIA wersje backendu ma w `series_state` fantomowe zero:
    parser zapisywal wtedy `percent: 0` jako pomiar. Po wdrozeniu ten wiersz nadal tam
    lezy — dopoki miernik jest wycofany, nie przyjdzie zadna nowa wartosc, ktora by go
    nadpisala. Widok musi go odrzucic sam, na podstawie `last_extra`."""
    from sqlalchemy import update as sa_update

    from app.models import SeriesState, UsageSeries
    from tests.team import USAGE_WITHDRAWN, team_payload

    await ingest_one(db, machine_name="desktop", payload=team_payload(USAGE_WITHDRAWN))
    await db.commit()
    series = (await db.execute(
        select(UsageSeries).where(UsageSeries.series_key == "spend:org")
    )).scalar_one()
    # Dokladnie to, co zostawila poprzednia wersja: zero jako pomiar, przy powodzie w extra.
    await db.execute(sa_update(SeriesState)
                     .where(SeriesState.series_id == series.id)
                     .values(last_utilization=0))
    await db.execute(sa_update(UsageSeries)
                     .where(UsageSeries.id == series.id).values(ever_non_null=True))
    await db.commit()

    spend = {s["seriesKey"]: s
             for s in (await wire(db))["accounts"][0]["series"]}["spend:org"]
    assert spend["utilization"] is None
    assert spend["rawUtilization"] is None, "fantomowe zero z bazy nie moze wrocic na drut"
    assert spend["unavailableReason"] == "org_level_disabled_until"
