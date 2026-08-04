"""Brama autoryzacji dla API odczytu.

Trzy tryby, wybierane przez AUTH_MODE:

  `none`   — bez bramy. Sensowne wylacznie wtedy, gdy usluga nie jest osiagalna z sieci:
             port na petli zwrotnej albo zaufany segment LAN. ALLOWED_EMAILS nie ma tu
             zastosowania, bo zadnego adresu nie ma skad wziac.
  `header` — proxy odwrotne juz uwierzytelnilo i podaje adres w naglowku
             (AUTH_EMAIL_HEADER). Wolno tylko za proxy, ktore ten naglowek USUWA z zadan
             przychodzacych; inaczej kazdy nazwie sie kim zechce.
  `verify` — pytamy usluge tozsamosci przez JSON (AUTH_VERIFY_URL), przekazujac
             ciasteczka. Nazwy pol odpowiedzi sa konfiguracja.

W trybach `header` i `verify` na wierzchu stoi jeszcze wlasna allowlista ALLOWED_EMAILS
(pusta = deny all, fail-safe): uwierzytelnienie mowi KTO, allowlista mowi KOMU wolno.

UWAGA: ten modul jest JEDYNA brama — przed backendem nie stoi zadne osobne `auth_request`.
Jego odpowiedz "401 z redirect_url" jest czescia publicznego kontraktu API, bo UI musi na
nia zareagowac samo. Nikt nie zwroci mu 302.
"""
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request, status
from loguru import logger

from app.config import settings

#: Tozsamosc w trybie `none`. Nie jest adresem i celowo nie wyglada na adres — gdyby
#: kiedys trafila do allowlisty albo do logu, ma byc od razu widac, ze nikt sie nie logowal.
ANONYMOUS = "anonymous"


@dataclass(frozen=True)
class CurrentUser:
    email: str
    verified_at: str | None


def _login_url() -> str | None:
    """Adres logowania albo None, gdy nie ma dokad odeslac.

    None jest pelnoprawnym wynikiem: instalacja bez zewnetrznego logowania nie ma zadnej
    strony, na ktora wypadaloby przekierowac, a wyslanie uzytkownika w domysle byle gdzie
    jest gorsze niz powiedzenie mu wprost, ze nie jest zalogowany.
    """
    if not settings.auth_login_url:
        return None
    base = settings.public_origin.rstrip("/")
    rd = quote(base + settings.app_base_path.rstrip("/") + "/", safe="")
    return settings.auth_login_url.replace("{rd}", rd)


def _not_authenticated(redirect: str | None = None) -> HTTPException:
    detail: dict[str, str] = {"reason": "not-authenticated"}
    url = redirect or _login_url()
    if url:
        detail["redirect_url"] = url
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _authorize(email: str | None, verified_at: str | None) -> CurrentUser:
    """Wspolna koncowka dla `header` i `verify`: mamy adres, pytamy czy wolno."""
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"reason": "missing-email"},
        )
    if email.lower() not in settings.allowed_emails:
        logger.warning("Odrzucono dostep dla adresu spoza allowlisty: {}", email)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"reason": "email-not-allowed"},
        )
    return CurrentUser(email=email, verified_at=verified_at)


async def _call_verify(cookie: str) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(
                settings.auth_verify_url,
                headers={"Cookie": cookie} if cookie else {},
            )
        except httpx.RequestError as exc:
            logger.error("Usluga tozsamosci nieosiagalna: {}", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"reason": "sso-unreachable"},
            ) from exc
    try:
        payload = response.json()
    except ValueError:
        # Odpowiedzia bywa strona HTML, nie JSON — to nie blad, tylko brak danych.
        payload = {}
    return response.status_code, payload


async def _verify(request: Request) -> CurrentUser:
    if not settings.auth_verify_url:
        logger.error("AUTH_MODE=verify, ale AUTH_VERIFY_URL jest pusty")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"reason": "sso-unavailable"},
        )

    status_code, data = await _call_verify(request.headers.get("Cookie", ""))

    if status_code == 200:
        verified_at = (
            data.get(settings.auth_verified_at_field)
            if settings.auth_verified_at_field
            else None
        )
        return _authorize(data.get(settings.auth_email_field), verified_at)

    # Zarowno 401, jak i 403: usluga tozsamosci, ktora odpowiada niezalogowanemu strona
    # logowania zamiast JSON-em, uzywa raz jednego, raz drugiego. Tutaj backend JEST brama,
    # wiec oba musza znaczyc "nie zalogowany", a nie "awaria".
    if status_code in (401, 403):
        redirect = (
            data.get(settings.auth_redirect_field)
            if settings.auth_redirect_field
            else None
        )
        raise _not_authenticated(redirect)

    logger.warning("Usluga tozsamosci zwrocila nieoczekiwany status: {}", status_code)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"reason": "sso-unavailable"},
    )


async def require_authorized_user(request: Request) -> CurrentUser:
    if settings.auth_mode == "none":
        return CurrentUser(email=ANONYMOUS, verified_at=None)

    if settings.auth_mode == "header":
        email = request.headers.get(settings.auth_email_header, "").strip()
        if not email:
            # Brak naglowka to "nie zalogowany", nie "zly adres" — proxy po prostu nikogo
            # nie przepuscilo.
            raise _not_authenticated()
        return _authorize(email, None)

    return await _verify(request)
