"""Authorization modes: AUTH_MODE.

Two things are worth testing here, because both of them fail silently.

The first: missing configuration must not end in open access. Compose substitutes an
EMPTY STRING for an unset environment variable, so "I never set AUTH_MODE" and "I set
AUTH_MODE to nothing" reach the application as different things, yet both have to end
the same way — with no startup. A `str` type would take an empty value without a word.

The second: every mode must admit exactly whom it promises. `none` with nothing at all,
`header` only with the header from the proxy and only an address from the allowlist.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.config import Settings, settings


@pytest_asyncio.fixture
async def api():
    """A client with NO `require_authorized_user` override — otherwise we would test a stub.

    `/api/me` does not touch the database, so it needs no session: the gate answers alone.
    """
    import app.main as main

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --------------------------------------------------------------------------- configuration
def test_brak_auth_mode_nie_pozwala_wstac(monkeypatch):
    monkeypatch.delenv("AUTH_MODE", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_puste_auth_mode_nie_pozwala_wstac(monkeypatch):
    """The Compose case: `AUTH_MODE: ${AUTH_MODE}` with no such variable in the environment."""
    monkeypatch.setenv("AUTH_MODE", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_nieznana_nazwa_trybu_nie_pozwala_wstac(monkeypatch):
    """A typo must stop the container, not fall into the "nothing matches" branch."""
    monkeypatch.setenv("AUTH_MODE", "None")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# --------------------------------------------------------------------------- none
async def test_tryb_none_wpuszcza_bez_ciasteczka(api, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "none")
    r = await api.get("/api/me")
    assert r.status_code == 200, r.text


async def test_tryb_none_nie_patrzy_na_allowliste(api, monkeypatch):
    """An empty allowlist is deny-all everywhere else. Here there is no address for it to
    compare against — and better that this be written down than that somebody add the
    condition 'for consistency' and shut the local mode behind a blank 403."""
    monkeypatch.setattr(settings, "auth_mode", "none")
    monkeypatch.setattr(settings, "allowed_emails_raw", "")
    assert (await api.get("/api/me")).status_code == 200


# --------------------------------------------------------------------------- header
async def test_tryb_header_bez_naglowka_to_401(api, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "header")
    r = await api.get("/api/me")
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == "not-authenticated"


async def test_tryb_header_bez_adresu_logowania_nie_podaje_redirect_url(api, monkeypatch):
    """An empty AUTH_LOGIN_URL = nowhere to send anyone. The UI must then show the error
    in place, so the contract must not carry a guessed address."""
    monkeypatch.setattr(settings, "auth_mode", "header")
    monkeypatch.setattr(settings, "auth_login_url", "")
    assert "redirect_url" not in (await api.get("/api/me")).json()["detail"]


async def test_tryb_header_z_adresem_logowania_podaje_redirect_url(api, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "header")
    monkeypatch.setattr(settings, "auth_login_url", "https://example.org/login?rd={rd}")
    monkeypatch.setattr(settings, "public_origin", "https://example.org")
    monkeypatch.setattr(settings, "app_base_path", "/claude-usage")
    detail = (await api.get("/api/me")).json()["detail"]
    assert detail["redirect_url"] == (
        "https://example.org/login?rd=https%3A%2F%2Fexample.org%2Fclaude-usage%2F"
    )


async def test_tryb_header_wpuszcza_adres_z_allowlisty(api, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "header")
    monkeypatch.setattr(settings, "allowed_emails_raw", "a@b.pl")
    r = await api.get("/api/me", headers={"X-Forwarded-Email": "a@b.pl"})
    assert r.status_code == 200
    assert r.json()["email"] == "a@b.pl"


async def test_tryb_header_odrzuca_adres_spoza_allowlisty(api, monkeypatch):
    """Authentication says WHO, the allowlist says WHO IS ALLOWED. 403, not 401 — sending
    an already logged-in user back to the login page gives a loop."""
    monkeypatch.setattr(settings, "auth_mode", "header")
    monkeypatch.setattr(settings, "allowed_emails_raw", "a@b.pl")
    r = await api.get("/api/me", headers={"X-Forwarded-Email": "ktos.inny@b.pl"})
    assert r.status_code == 403
    assert r.json()["detail"]["reason"] == "email-not-allowed"


async def test_nazwa_naglowka_jest_konfiguracja(api, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "header")
    monkeypatch.setattr(settings, "auth_email_header", "X-Auth-Request-Email")
    monkeypatch.setattr(settings, "allowed_emails_raw", "a@b.pl")
    assert (await api.get("/api/me", headers={"X-Forwarded-Email": "a@b.pl"})).status_code == 401
    assert (await api.get("/api/me", headers={"X-Auth-Request-Email": "a@b.pl"})).status_code == 200


# --------------------------------------------------------------------------- verify
async def test_tryb_verify_bez_adresu_uslugi_nie_zgaduje(api, monkeypatch):
    """There is nobody to ask. This is a configuration failure, not a missing session —
    503, not 401, because a redirect to the login page would fix nothing here."""
    monkeypatch.setattr(settings, "auth_mode", "verify")
    monkeypatch.setattr(settings, "auth_verify_url", "")
    r = await api.get("/api/me")
    assert r.status_code == 503
    assert r.json()["detail"]["reason"] == "sso-unavailable"


async def test_tryb_verify_czyta_pola_odpowiedzi_z_konfiguracji(api, monkeypatch):
    """Every identity service names these fields differently, so the names are
    configuration. The test passes its own and checks the module hardcodes none."""
    import app.sso as sso

    async def fake_verify(cookie: str):
        return 200, {"kto": "a@b.pl", "kiedy": "2026-01-01T00:00:00Z"}

    monkeypatch.setattr(settings, "auth_mode", "verify")
    monkeypatch.setattr(settings, "auth_verify_url", "http://identity/api/verify")
    monkeypatch.setattr(settings, "auth_email_field", "kto")
    monkeypatch.setattr(settings, "auth_verified_at_field", "kiedy")
    monkeypatch.setattr(settings, "allowed_emails_raw", "a@b.pl")
    monkeypatch.setattr(sso, "_call_verify", fake_verify)

    r = await api.get("/api/me")
    assert r.status_code == 200
    assert r.json() == {"email": "a@b.pl", "verifiedAt": "2026-01-01T00:00:00Z"}


@pytest.mark.parametrize("kod", [401, 403])
async def test_tryb_verify_traktuje_401_i_403_jako_brak_sesji(api, monkeypatch, kod):
    """A service that answers a logged-out visitor with a login page instead of JSON uses
    now one code, now the other. The backend IS the gate here, so both mean "not logged in"."""
    import app.sso as sso

    async def fake_verify(cookie: str):
        return kod, {}

    monkeypatch.setattr(settings, "auth_mode", "verify")
    monkeypatch.setattr(settings, "auth_verify_url", "http://identity/api/verify")
    monkeypatch.setattr(sso, "_call_verify", fake_verify)

    r = await api.get("/api/me")
    assert r.status_code == 401
    assert r.json()["detail"]["reason"] == "not-authenticated"


async def test_tryb_verify_oddaje_adres_logowania_podany_przez_usluge(api, monkeypatch):
    import app.sso as sso

    async def fake_verify(cookie: str):
        return 401, {"gdzie": "https://identity.example.org/start"}

    monkeypatch.setattr(settings, "auth_mode", "verify")
    monkeypatch.setattr(settings, "auth_verify_url", "http://identity/api/verify")
    monkeypatch.setattr(settings, "auth_redirect_field", "gdzie")
    monkeypatch.setattr(sso, "_call_verify", fake_verify)

    detail = (await api.get("/api/me")).json()["detail"]
    assert detail["redirect_url"] == "https://identity.example.org/start"
