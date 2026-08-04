"""Endpointy diagnostyczne — regresja na to, ze migracja `0002_cli_source` je wywrocila.

Wersja 3 sondy nie wysyla zadnego zadania do Anthropic, wiec migracja usunela kolumny
opisujace odpowiedz HTTP (`http_status`, `request_id`, `rl_status`) oraz `cc_version`,
ktora i tak trzymala stala z kodu. `routers/read.py` czytal je dalej — czyli `/api/machines`
i `/api/batches` oddawaly 500 od momentu wdrozenia migracji.

Nie zlapal tego zaden test, bo tych endpointow nie dotykal ZADEN. Diagnostyka zostala
swiadomie przy `curl` (README, `docs/API.md` § 10), tylko z tej decyzji wyszlo
milczaco, ze nikt jej nie sprawdza. Ten plik zamyka luke: wola kazdy endpoint odczytu
i sprawdza, ze w ogole odpowiada.

Dlatego asercje sa celowo plytkie — to nie jest test ksztaltu odpowiedzi diagnostycznej,
tylko strazak na "kolumna znikla z modelu, a router nadal ja czyta". Taki blad zawsze
wychodzi juz przy pierwszym wywolaniu.
"""
from datetime import timedelta

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.test_ingest_e2e import (  # noqa: F401 — fixture `db` przychodzi razem z nimi
    ACCOUNT_MAX, db, ingest_one, payload, utcnow,
)


@pytest_asyncio.fixture
async def api(db):
    """Aplikacja z podmieniona sesja i pominietym SSO — jak w test_history_endpoint."""
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
    now = utcnow()
    for offset in (120, 0):
        await ingest_one(db, machine_name="desktop",
                         payload=payload(captured_at=now - timedelta(seconds=offset)))
    await db.commit()


async def test_kazdy_endpoint_odczytu_odpowiada(api, dane):
    """Jedna petla po wszystkich — 500 z martwej kolumny wychodzi tu natychmiast."""
    for path in ("/api/me", "/api/accounts", "/api/machines", "/api/series",
                 "/api/batches", "/api/events", "/api/stats"):
        r = await api.get(path)
        assert r.status_code == 200, "%s -> %s %s" % (path, r.status_code, r.text)


async def test_batches_niosa_proweniencje_pomiaru_zamiast_kodow_http(api, dane):
    """To jest jedyne miejsce, w ktorym widac, czy pomiar mial swieze procenty ze stdout
    (`cli_merged`), czy poszedl sam cache (`cli_usage_cache`, do 5 min stary). Bez tego
    spadek rozdzielczosci z minuty do pieciu jest niewykrywalny — dane nadal plyna."""
    b = (await api.get("/api/batches")).json()
    assert b, "ingest musial zostawic batche"
    assert b[0]["measurementSource"] == "cli_merged"
    assert b[0]["cacheAgeS"] == 42 and b[0]["freshAgeS"] == 7
    assert b[0]["scriptVersion"] == 5
    # Pola po martwych kolumnach nie moga wrocic tylnymi drzwiami jako None.
    assert not ({"httpStatus", "requestId", "rlStatus", "ccVersion"} & set(b[0]))


async def test_machines_nie_udaje_ze_zna_wersje_claude_code(api, dane):
    """`cc_version` bralo sie z UA_VERSION sondy, czyli ze stalej zaszytej w kodzie —
    ta sama wartosc dla kazdej maszyny, niezalezna od tego, co tam faktycznie chodzi.
    `scriptVersion` zostaje, bo mowi prawde: po tym poznajemy stan wdrozenia sondy."""
    m = (await api.get("/api/machines")).json()
    assert m and m[0]["scriptVersion"] == 5
    assert "ccVersion" not in m[0]
