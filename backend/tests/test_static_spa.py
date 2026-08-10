"""Serving the UI from the backend: the SPA fallback must not swallow the API.

The regression this covers (caught at the first deployment): the catch-all
`/{full_path:path}` catches everything the routers did not match — so a typo in an
endpoint address returned HTTP 200 with index.html instead of a 404 JSON. A client would
try to parse an HTML page as JSON and report the error in a completely different place.
"""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The app with a BUILT UI — the static directory is a make-believe one."""
    static = tmp_path / "static"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<!doctype html><title>Claude Usage</title>", "utf-8")
    (static / "assets" / "index-abc123.js").write_text("console.log(1)", "utf-8")
    (static / "favicon.svg").write_text("<svg/>", "utf-8")

    import app.main as main

    # The static mount and the fallback route are settled at module IMPORT, so the directory
    # must be known before the reload — hence an environment variable, not an attribute swap.
    monkeypatch.setenv("STATIC_DIR", str(static))
    importlib.reload(main)
    try:
        with TestClient(main.app) as c:
            yield c
    finally:
        monkeypatch.delenv("STATIC_DIR", raising=False)
        importlib.reload(main)      # other tests must see the app with no statics


def test_korzen_oddaje_powloke_spa(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_sciezka_routera_tez_oddaje_powloke(client):
    """A deep link at /history must work after a page refresh, not give a 404."""
    r = client.get("/history")
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
    """Monitoring and `curl -I` use HEAD; @app.get does not add it by itself."""
    assert client.head("/").status_code == 200


def test_powloka_nie_jest_cache_owana_a_assety_sa(client):
    assert client.get("/").headers["cache-control"] == "no-store"
    r = client.get("/assets/index-abc123.js")
    assert r.status_code == 200
    assert "immutable" in r.headers["cache-control"]


def test_plik_z_dist_jest_oddawany_wprost(client):
    r = client.get("/favicon.svg")
    assert r.status_code == 200 and "svg" in r.headers["content-type"]


@pytest.mark.parametrize("sciezka", ["/openapi.json", "/docs", "/redoc"])
def test_schemat_i_przegladarki_api_sa_wylaczone(client, sciezka):
    """The reverse proxy passes through the container's whole root, while the gate sits on
    the endpoints' dependency — not on these routes. An exposed schema would hand the full
    set of paths, field names and error shapes to anyone without a session.

    The assertion looks at the CONTENT, not at the response code: the SPA catch-all returns
    index.html with code 200, so `status_code != 200` would attest to nothing here.
    """
    r = client.get(sciezka)
    assert "openapi" not in r.text
    assert "swagger" not in r.text.lower()
    assert "/api/status" not in r.text


def test_wyjscie_ze_katalogu_statykow_nie_jest_mozliwe(client):
    """A path with ../ must not lead outside dist — the shell is returned, not a host file."""
    r = client.get("/../../etc/passwd")
    assert r.status_code in (200, 404)
    assert "passwd" not in r.text
    assert "root:" not in r.text
