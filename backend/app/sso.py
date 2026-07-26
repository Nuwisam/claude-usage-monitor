"""Warstwa autoryzacji SSO.

Rozmawia z API /api/verify oauth2-proxy (identity proxy). Kontrakt tej integracji:
  - zawsze uzywamy API JSON; nigdy nie ufamy surowym naglowkom auth (moga sie zmienic),
  - nigdy nie wystawiamy pol `x-auth-*` poza ten modul — mapujemy je na CurrentUser,
  - dodatkowo egzekwujemy wlasna allowliste ALLOWED_EMAILS (fail-safe: pusta = deny all).

UWAGA: bez frontendu z nginx `auth_request` ten modul jest JEDYNA brama SSO, a jego
zachowanie "401 z redirect_url" jest czescia publicznego kontraktu API — UI musi na nie
zareagowac przekierowaniem, bo nikt nie zwroci mu 302.
"""
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request, status
from loguru import logger

from app.config import settings


@dataclass(frozen=True)
class CurrentUser:
    email: str
    verified_at: str | None


def _login_url() -> str:
    """Apache proxuje /oauth2/start na login.example.org (conf-available/identity-proxy.conf),
    wiec URL logowania budujemy wzgledem naszego wlasnego origin, nie SSO."""
    base = settings.public_origin.rstrip("/")
    rd = quote(base + settings.app_base_path.rstrip("/") + "/", safe="")
    return "%s/oauth2/start?rd=%s" % (base, rd)


async def _call_verify(cookie: str) -> tuple[int, dict]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(
                settings.sso_verify_url,
                headers={"Cookie": cookie} if cookie else {},
            )
        except httpx.RequestError as exc:
            logger.error("SSO verify nieosiagalny: {}", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"reason": "sso-unreachable"},
            ) from exc
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    return response.status_code, payload


async def require_authorized_user(request: Request) -> CurrentUser:
    cookie = request.headers.get("Cookie", "")
    status_code, data = await _call_verify(cookie)

    if status_code == 200:
        email = data.get("x-auth-email")
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
        return CurrentUser(email=email, verified_at=data.get("x-auth-timestamp"))

    # UWAGA: oauth2-proxy w tym wdrozeniu zwraca dla niezalogowanego **403 ze strona HTML**,
    # a nie 401 z JSON-em. Wczesniejsza wersja obslugiwala tylko 401 —
    # tam to nie boli, bo brama jest nginx auth_request i backend nie widzi takich zadan.
    # Tutaj backend JEST brama, wiec 403 musi znaczyc "nie zalogowany", nie "awaria".
    if status_code in (401, 403):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "reason": "not-authenticated",
                # Body bywa HTML-em, wiec redirect_url zwykle trzeba zbudowac samemu.
                "redirect_url": (data.get("x-auth-redirect-url")
                                 or data.get("x-auth-login-url")
                                 or _login_url()),
            },
        )

    logger.warning("SSO verify zwrocil nieoczekiwany status: {}", status_code)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"reason": "sso-unavailable"},
    )
