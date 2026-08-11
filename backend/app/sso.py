"""Authorization gate for the read API.

Three modes, selected by AUTH_MODE:

  `none`   — no gate. Sensible only when the service is not reachable from the network:
             a loopback port or a trusted LAN segment. ALLOWED_EMAILS does not apply here,
             because there is no email address to be had from anywhere.
  `header` — the reverse proxy has already authenticated and supplies the email address in a
             header (AUTH_EMAIL_HEADER). Permitted only behind a proxy that STRIPS that
             header from incoming requests; otherwise anyone calls themselves whatever
             they like.
  `verify` — the identity service is queried over JSON (AUTH_VERIFY_URL), forwarding the
             cookies. The response field names are configuration.

In `header` and `verify` mode there is still a separate ALLOWED_EMAILS allowlist on top
(empty = deny all, fail-safe): authentication says WHO, the allowlist says who is PERMITTED.

NOTE: this module is the ONLY gate — no separate `auth_request` sits in front of the
backend. Its "401 with redirect_url" response is part of the public API contract, because the
UI has to react to it on its own. Nobody will hand it a 302.
"""
from dataclasses import dataclass
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request, status
from loguru import logger

from app.config import settings

#: The identity in `none` mode. Not an email address, and deliberately not email-shaped — if it
#: ever lands in the allowlist or in a log, it must be visible at once that nobody logged in.
ANONYMOUS = "anonymous"


@dataclass(frozen=True)
class CurrentUser:
    email: str
    verified_at: str | None


def _login_url() -> str | None:
    """The login URL, or None when there is nowhere to send the user back to.

    None is a fully valid result: an installation without external login has no page that
    would be worth redirecting to, and sending the user off to some default somewhere is
    worse than telling them outright that they are not logged in.
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
    """Shared tail for `header` and `verify`: the email is known, now ask whether it is allowed."""
    if not email:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"reason": "missing-email"},
        )
    if email.lower() not in settings.allowed_emails:
        logger.warning("Denied access to an email outside the allowlist: {}", email)
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
            logger.error("Identity service unreachable: {}", exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"reason": "sso-unreachable"},
            ) from exc
    try:
        payload = response.json()
    except ValueError:
        # The response is sometimes an HTML page, not JSON — not an error, just no data.
        payload = {}
    return response.status_code, payload


async def _verify(request: Request) -> CurrentUser:
    if not settings.auth_verify_url:
        logger.error("AUTH_MODE=verify, but AUTH_VERIFY_URL is empty")
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

    # Both 401 and 403: an identity service that answers an unauthenticated caller with a login
    # page instead of JSON uses one on one occasion and the other on the next. Here the backend
    # IS the gate, so both must mean "not logged in", not "failure".
    if status_code in (401, 403):
        redirect = (
            data.get(settings.auth_redirect_field)
            if settings.auth_redirect_field
            else None
        )
        raise _not_authenticated(redirect)

    logger.warning("Identity service returned an unexpected status: {}", status_code)
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
            # A missing header means "not logged in", not "bad email address" — the proxy simply
            # did not let anyone through.
            raise _not_authenticated()
        return _authorize(email, None)

    return await _verify(request)
