"""Serwowanie UI z backendu: fallback SPA nie moze zjadac API.

Regresja, ktora to pokrywa (zlapana przy pierwszym wdrozeniu): catch-all
`/{full_path:path}` lapie wszystko, czego nie dopasowaly routery — wiec literowka
w adresie endpointu oddawala HTTP 200 z index.html zamiast 404 JSON. Klient probowalby
sparsowac strone HTML jako JSON i zglosil blad w zupelnie innym miejscu.
"""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Aplikacja ze ZBUDOWANYM UI — katalog statyków tworzymy na niby."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>Claude Usage</title>", "utf-8")
    (static / "assets" / "index-abc123.js").write_text("console.log(1)", "utf-8")
    (static / "favicon.svg").write_text("<svg/>", "utf-8")

    import app.main as main

    # Mount statykow i trasa fallbacku zapadaja przy IMPORCIE modulu, wiec katalog musi
    # byc znany przed reloadem — stad zmienna srodowiskowa, a nie podmiana atrybutu.
    monkeypatch.setenv("STATIC_DIR", str(static))
    importlib.reload(main)
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        monkeypatch.delenv("STATIC_DIR", raising=False)
        importlib.reload(main)      # inne testy maja widziec aplikacje bez statykow


def test_korzen_oddaje_powloke_spa(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_sciezka_routera_tez_oddaje_powloke(client):
    """Deep link w /historia musi dzialac po odswiezeniu strony, nie dawac 404."""
    r = client.get("/historia")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_nieznana_sciezka_api_daje_404_json_a_nie_html(client):
    r = client.get("/api/nieistnieje")
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")
    assert r.json()["detail"]["reason"] == "not-found"


def test_samo_api_bez_endpointu_tez_nie_jest_powloka(client):
    assert client.get("/api").status_code == 404
    assert client.get("/api/").status_code == 404


def test_health_dalej_dziala_pod_statykami(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_head_jest_obslugiwany(client):
    """Monitoring i `curl -I` uzywaja HEAD; @app.get sam go nie dodaje."""
    assert client.head("/").status_code == 200


def test_powloka_nie_jest_cache_owana_a_assety_sa(client):
    assert client.get("/").headers["cache-control"] == "no-store"
    r = client.get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]


def test_plik_z_dist_jest_oddawany_wprost(client):
    r = client.get("/favicon.svg")
    assert r.status_code == 200 and "svg" in r.headers["content-type"]


def test_wyjscie_ze_katalogu_statykow_nie_jest_mozliwe(client):
    """Sciezka z ../ nie moze wyprowadzic poza dist — oddajemy powloke, nie plik hosta."""
    r = client.get("/../../etc/passwd")
    assert r.status_code in (200, 404)
    assert "passwd" not in r.text
    assert "root:" not in r.text
