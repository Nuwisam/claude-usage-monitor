"""Tryby autoryzacji: AUTH_MODE.

Dwie rzeczy sa tu warte testu, bo obie zawodza cicho.

Pierwsza: brak konfiguracji nie moze konczyc sie otwartym dostepem. Compose podstawia
PUSTY CIAG za nieustawiona zmienna srodowiskowa, wiec "nie ustawilem AUTH_MODE" i
"ustawilem AUTH_MODE na nic" docieraja do aplikacji jako rozne rzeczy, a skonczyc sie
musza tak samo — brakiem startu. Typ `str` przyjalby pusta wartosc bez slowa.

Druga: kazdy tryb musi wpuszczac dokladnie tego, kogo obiecuje. `none` bez niczego,
`header` wylacznie z naglowkiem od proxy i wylacznie adres z allowlisty.
"""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.config import Settings, settings


@pytest_asyncio.fixture
async def api():
    """Klient BEZ podmiany `require_authorized_user` — inaczej testowalibysmy atrape.

    `/api/me` nie dotyka bazy, wiec nie potrzebuje sesji: odpowiada sama brama.
    """
    import app.main as main

    transport = ASGITransport(app=main.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --------------------------------------------------------------------------- konfiguracja
def test_brak_auth_mode_nie_pozwala_wstac(monkeypatch):
    monkeypatch.delenv("AUTH_MODE", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_puste_auth_mode_nie_pozwala_wstac(monkeypatch):
    """To jest przypadek z Compose: `AUTH_MODE: ${AUTH_MODE}` bez zmiennej w srodowisku."""
    monkeypatch.setenv("AUTH_MODE", "")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_nieznana_nazwa_trybu_nie_pozwala_wstac(monkeypatch):
    """Literowka ma zatrzymac kontener, a nie wpasc w gałąź "nic nie pasuje"."""
    monkeypatch.setenv("AUTH_MODE", "None")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


# --------------------------------------------------------------------------- none
async def test_tryb_none_wpuszcza_bez_ciasteczka(api, monkeypatch):
    monkeypatch.setattr(settings, "auth_mode", "none")
    r = await api.get("/api/me")
    assert r.status_code == 200, r.text


async def test_tryb_none_nie_patrzy_na_allowliste(api, monkeypatch):
    """Pusta allowlista to deny all wszedzie indziej. Tutaj nie ma adresu, ktory mialaby
    porownac — i lepiej, zeby bylo to zapisane, niz zeby ktos dodal ten warunek 'dla
    spojnosci' i zamknal tryb lokalny na gluchy 403."""
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
    """Pusty AUTH_LOGIN_URL = nie ma dokad odeslac. UI ma wtedy pokazac blad na miejscu,
    wiec kontrakt nie moze zawierac zgadnietego adresu."""
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
    """Uwierzytelnienie mowi KTO, allowlista mowi KOMU wolno. 403, nie 401 — odsylanie
    zalogowanego na logowanie daje petle."""
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
    """Nie ma kogo zapytac. To awaria konfiguracji, a nie brak sesji — 503, nie 401,
    bo przekierowanie na logowanie niczego by tu nie naprawilo."""
    monkeypatch.setattr(settings, "auth_mode", "verify")
    monkeypatch.setattr(settings, "auth_verify_url", "")
    r = await api.get("/api/me")
    assert r.status_code == 503
    assert r.json()["detail"]["reason"] == "sso-unavailable"


async def test_tryb_verify_czyta_pola_odpowiedzi_z_konfiguracji(api, monkeypatch):
    """Kazda usluga tozsamosci nazywa te pola inaczej, wiec nazwy sa konfiguracja.
    Test przekazuje wlasne i sprawdza, ze modul nie ma zadnych zaszytych."""
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
    """Usluga, ktora odpowiada niezalogowanemu strona logowania zamiast JSON-em, uzywa raz
    jednego kodu, raz drugiego. Backend JEST tu brama, wiec oba znacza "nie zalogowany"."""
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
