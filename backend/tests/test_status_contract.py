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

from tests.test_ingest_e2e import (  # noqa: F401 — fixture `db` przychodzi razem z nimi
    REAL, db, ingest_one, payload, utcnow, with_util,
)

from app.services.status import CONTRACT_VERSION, build_status

# ISO-8601 z offsetem: "2026-07-26T19:07:37.564772Z" albo "...+00:00"
ISO_TZ = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")
DATE_KEYS = ("At", "Now", "resetsAt")


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
    assert (await wire(db))["contractVersion"] == CONTRACT_VERSION == 2


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
