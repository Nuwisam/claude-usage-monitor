"""Realne payloady z konta Team — trzy stany kredytow, zdjete z tej samej maszyny.

NIE `test_*.py`, wiec pytest tego nie zbiera; testy importuja `tests.team`.

Dwie rodziny fixture'ow, bo czytaja je dwaj rozni konsumenci:

  * WYCINEK `usage` (`usage_team_*.json`) — to je `parse_usage`. Dokladnie to, co sonda
    wysyla w polu `usage`, czyli `cachedUsageUtilization.utilization`.
  * PELNY `.claude.json` (`claude_json_*.json`) — to czyta SONDA. Rozmiar i uklad pliku
    sa tu czescia przypadku testowego: `_extract_block` chodzi po tekscie `text.find`-em,
    a plik ma duplikaty kluczy roznjace sie wielkoscia liter (`z:/...` i `Z:/...`).

Tozsamosci sa wyczyszczone (e-mail, UUID-y, userID, machineID, kod referencyjny, sciezki
projektow, tresci polecen). NIETKNIETE zostaly `cachedUsageUtilization` i
`cachedExtraUsageDisabledReason` — bajt w bajt poza samym `accountUuid`, ktory MUSI byc
ten sam w `oauthAccount` i w `cachedUsageUtilization`, bo ingest kluczuje po nim
(zasada 7).

Trzy stany, ktore te pliki utrwalaja:

  |                              | active | pool_exhausted | withdrawn                  |
  |------------------------------|--------|----------------|----------------------------|
  | spend.enabled                | true   | true           | false                      |
  | spend.percent                | 93     | 100            | 0  <- fantom               |
  | spend.used / limit           | 277,95 / 300,00 EUR    | 300,04 / 300,00 | 0,00 / null |
  | spend.disabled_reason        | null   | null           | org_level_disabled_until   |
  | extra_usage.spend_limit_reached | false | false       | true                       |
  | cachedExtraUsageDisabledReason  | null  | org_spend_cap_reached | org_level_disabled_until |

Kolumna srodkowa jest tu po to, zeby pilnowac, czego zmieniac NIE WOLNO: wyczerpana
WLASNA pula to dzialajacy licznik, ktory mowi prawde (100%), a nie wycofany miernik.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"

# --- rodzina A: wycinek `usage`, wejscie dla parse_usage ---------------------
USAGE_ACTIVE = FIXTURES / "usage_team_active.json"
USAGE_POOL_EXHAUSTED = FIXTURES / "usage_team_pool_exhausted.json"
USAGE_WITHDRAWN = FIXTURES / "usage_team_withdrawn.json"

# --- rodzina B: pelny ~/.claude.json, wejscie dla sondy ----------------------
CLAUDE_JSON_CREDITS_OK = FIXTURES / "claude_json_credits_ok.json"
CLAUDE_JSON_POOL_EXHAUSTED = FIXTURES / "claude_json_pool_exhausted.json"
CLAUDE_JSON_COMPANY_EXHAUSTED = FIXTURES / "claude_json_company_exhausted.json"

# (plik `.claude.json`, oczekiwany `cachedExtraUsageDisabledReason`) — trzy stany.
CLAUDE_JSON_STATES = [
    (CLAUDE_JSON_CREDITS_OK, None),
    (CLAUDE_JSON_POOL_EXHAUSTED, "org_spend_cap_reached"),
    (CLAUDE_JSON_COMPANY_EXHAUSTED, "org_level_disabled_until"),
]

# Konto z tych samych plikow, przelozone na pola, ktore sklada sonda
# (client/usage-probe.py:571). Team, bo tylko na Team `spend` jest wiazacym limitem.
ACCOUNT_TEAM_REAL = {
    "uuid": "00000000-0000-4000-8000-000000000001",
    "email": "usage-monitor@example.test",
    "display_name": "EX",
    "org_uuid": "00000000-0000-4000-8000-000000000002",
    "org_name": "Example Org",
    "org_type": "claude_team",
    "seat_tier": "team_tier_1",
    "org_rate_limit_tier": "default_raven",
    "user_rate_limit_tier": "default_claude_max_5x",
    "extra_usage_enabled": True,
}


def usage(path: Path) -> dict[str, Any]:
    """Swieza KOPIA wycinka `usage`. Kopia, bo testy modyfikuja payloady w miejscu."""
    return json.loads(path.read_text("utf-8"))


def spend_block(payload: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(payload["spend"])


def team_payload(usage_path: Path = USAGE_ACTIVE, *, captured_at: datetime | None = None,
                 account: dict | None = None, event: str = "PostToolUse",
                 extra_usage_disabled_reason: Any = ...) -> dict[str, Any]:
    """Kompletny payload sondy wokol realnego wycinka `usage`.

    `extra_usage_disabled_reason` domyslnie NIE JEST dokladany: sonda ponizej wersji 4
    tego pola nie przysyla i backend musi to przezyc. Podanie `None` to co innego niz
    pominiecie — to jawne "sprawdzilem, powodu nie ma".
    """
    meas: dict[str, Any] = {"source": "cli_merged", "cache_age_s": 42, "fresh_age_s": 7}
    if extra_usage_disabled_reason is not ...:
        meas["extra_usage_disabled_reason"] = extra_usage_disabled_reason
    from app.services.ingest import utcnow
    return {
        "account": ACCOUNT_TEAM_REAL if account is None else account,
        "token_meta": {"subscription_type": "team"},
        "captured_at": (captured_at or utcnow()).isoformat(),
        "client": {"host": "DESKTOP-TEAM", "script_version": 4, "exec_ms": 41},
        "hook": {"event": event, "session_id": "sess-team", "cwd": "z:/projects/x"},
        "measurement": meas,
        "usage": usage(usage_path),
    }
