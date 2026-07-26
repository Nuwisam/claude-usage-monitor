"""Autoryzacja ingestu — Bearer per maszyna.

Token identyfikuje MASZYNE, nie konto. Konto pochodzi z `account.uuid` w payloadzie,
wyliczonego z oauthAccount po stronie klienta.

Swiadomie NIE wiazemy tokenu z lista dozwolonych kont: skoro na jednej maszynie uzywasz
dwoch kont na zmiane, statyczna lista znow by sie rozjechala. Zamiast zakazu stosujemy
detekcje — pierwsze wystapienie nowej pary (maszyna, konto) generuje zdarzenie
`new_account_for_token`, widoczne w Diagnostics.

Filtr brzegowy `X-Ingest-Key` siedzi w Apache i odcina skanery, zanim dotkna Pythona.
To nie jest powazna kryptografia — prawdziwa autoryzacja jest tutaj.
"""
import hmac

from fastapi import Header, HTTPException, status

from app.config import settings


def require_ingest_token(authorization: str | None = Header(default=None)) -> str:
    """Zwraca nazwe maszyny albo rzuca 401."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"reason": "missing-bearer"},
        )
    presented = authorization.split(" ", 1)[1].strip()

    # Porownanie w stalym czasie dla KAZDEGO skonfigurowanego tokenu — bez wczesnego
    # wyjscia, zeby czas odpowiedzi nie zdradzal, ktory prefiks byl trafiony.
    machine: str | None = None
    for token, name in settings.ingest_tokens.items():
        if hmac.compare_digest(presented, token):
            machine = name
    if machine is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"reason": "invalid-token"},
        )
    return machine
