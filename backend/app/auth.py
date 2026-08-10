"""Ingest authorization — one Bearer token per machine.

The token identifies the MACHINE, not the account. The account comes from `account.uuid` in
the payload, derived from oauthAccount on the client side.

The token is deliberately NOT bound to a list of permitted accounts: when a single machine is
used with two accounts in rotation, a static list would drift out of date again. Instead of a
prohibition there is detection — the first occurrence of a new (machine, account) pair emits
a `new_account_for_token` event, visible in Diagnostics.

The `X-Ingest-Key` edge filter sits in Apache and cuts off scanners before they reach Python.
It is not serious cryptography — the real authorization is here.
"""
import hmac

from fastapi import Header, HTTPException, Request, status

from app.config import settings


def _match(presented: str, tokens: dict[str, str]) -> str | None:
    """Constant-time comparison against EVERY configured token — no early exit, so the
    response time cannot reveal which prefix was hit."""
    found: str | None = None
    for token, name in tokens.items():
        if hmac.compare_digest(presented, token):
            found = name
    return found


def _bearer(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1].strip()


def require_ingest_token(authorization: str | None = Header(default=None)) -> str:
    """Returns the machine name, or raises 401."""
    presented = _bearer(authorization)
    if presented is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"reason": "missing-bearer"},
        )
    machine = _match(presented, settings.ingest_tokens)
    if machine is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"reason": "invalid-token"},
        )
    return machine


async def require_stream_client(
    request: Request, authorization: str | None = Header(default=None),
) -> str:
    """Authorization for `/api/stream`: Bearer OR the SSO cookie. Returns a log label.

    Two paths, because the stream has two consumers with different capabilities: a browser
    cannot attach a header to `EventSource` (only the cookie goes out), and the desk panel
    has no SSO session. The presence of an `Authorization` header decides which path.

    `STREAM_TOKENS` is a SEPARATE secret from `INGEST_TOKENS`. The probe token is a
    WRITE-only credential today and is handed out in `%LOCALAPPDATA%` on every machine that runs
    the probe; accepting it here would silently turn it into a
    credential that reads the state of every account — widening the meaning of a key that
    is already in circulation and that nobody would rotate for this reason.

    SSO is called DIRECTLY rather than through `Depends`, because the path is chosen only
    after inspecting the header: with a valid token we must not touch the gate at all.
    The trade-off is that `dependency_overrides` does not cover it — tests patch
    `app.sso.require_authorized_user` instead.
    """
    presented = _bearer(authorization)
    if presented is not None:
        label = _match(presented, settings.stream_tokens)
        if label is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"reason": "invalid-token"},
            )
        return "token:%s" % label

    # Local import: sso.py pulls in httpx, and `auth.py` is also imported by the ingest
    # path, which has nothing to do with SSO.
    from app.sso import require_authorized_user

    user = await require_authorized_user(request)
    return "sso:%s" % user.email
