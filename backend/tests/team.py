"""Real payloads from a Team account — three credit states, taken from the same machine.

NOT `test_*.py`, so pytest does not collect it; the tests import `tests.team`.

Two families of fixtures, because two different consumers read them:

  * The `usage` SLICE (`usage_team_*.json`) — this is what `parse_usage` eats. Exactly
    what the probe sends in the `usage` field, that is `cachedUsageUtilization.utilization`.
  * The FULL `.claude.json` (`claude_json_*.json`) — this is what the PROBE reads. The size
    and the layout of the file are part of the test case here: `_extract_block` walks the
    text with `text.find`, and the file has duplicate keys differing in letter case.

i18n-keep: `backend/tests/fixtures/*.json` are BYTE-FROZEN and no wave of the Polish to
English migration may touch them. They are captures of a real payload, so their Polish
project paths and their duplicate keys differing only in letter case are the test case
itself, not text. `_extract_block` walks these files with `text.find`, which means the
size and the byte layout are load-bearing; editing one to look tidier changes what is
being tested without changing any assertion.

Identities are scrubbed (e-mail, UUIDs, userID, machineID, referral code, project paths,
command bodies). LEFT UNTOUCHED are `cachedUsageUtilization` and
`cachedExtraUsageDisabledReason` — byte for byte apart from `accountUuid` itself, which
MUST be the same in `oauthAccount` and in `cachedUsageUtilization`, because ingest keys
on it (rule 7).

The three states these files pin down:

  |                              | active | pool_exhausted | withdrawn                  |
  |------------------------------|--------|----------------|----------------------------|
  | spend.enabled                | true   | true           | false                      |
  | spend.percent                | 93     | 100            | 0  <- phantom              |
  | spend.used / limit           | 277.95 / 300.00 EUR    | 300.04 / 300.00 | 0.00 / null |
  | spend.disabled_reason        | null   | null           | org_level_disabled_until   |
  | extra_usage.spend_limit_reached | false | false       | true                       |
  | cachedExtraUsageDisabledReason  | null  | org_spend_cap_reached | org_level_disabled_until |

The middle column is here to guard what MUST NOT be changed: an exhausted OWN pool is a
working meter telling the truth (100%), not a withdrawn meter.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"

# --- family A: the `usage` slice, input for parse_usage ---------------------
USAGE_ACTIVE = FIXTURES / "usage_team_active.json"
USAGE_POOL_EXHAUSTED = FIXTURES / "usage_team_pool_exhausted.json"
USAGE_WITHDRAWN = FIXTURES / "usage_team_withdrawn.json"

# --- family B: the full ~/.claude.json, input for the probe -----------------
CLAUDE_JSON_CREDITS_OK = FIXTURES / "claude_json_credits_ok.json"
CLAUDE_JSON_POOL_EXHAUSTED = FIXTURES / "claude_json_pool_exhausted.json"
CLAUDE_JSON_COMPANY_EXHAUSTED = FIXTURES / "claude_json_company_exhausted.json"

# (the `.claude.json` file, the expected `cachedExtraUsageDisabledReason`) — three states.
CLAUDE_JSON_STATES = [
    (CLAUDE_JSON_CREDITS_OK, None),
    (CLAUDE_JSON_POOL_EXHAUSTED, "org_spend_cap_reached"),
    (CLAUDE_JSON_COMPANY_EXHAUSTED, "org_level_disabled_until"),
]

# The account from the same files, mapped onto the fields the probe assembles
# (client/usage-probe.py:571). Team, because `spend` is a binding limit only on Team.
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
    """A fresh COPY of the `usage` slice. A copy, because tests modify payloads in place."""
    return json.loads(path.read_text("utf-8"))


def spend_block(payload: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(payload["spend"])


def team_payload(usage_path: Path = USAGE_ACTIVE, *, captured_at: datetime | None = None,
                 account: dict | None = None, event: str = "PostToolUse",
                 extra_usage_disabled_reason: Any = ...) -> dict[str, Any]:
    """A complete probe payload around a real `usage` slice.

    `extra_usage_disabled_reason` is NOT added by default: a probe below version 4 does
    not send this field and the backend has to survive that. Passing `None` is something
    other than omitting it — it is an explicit "I checked, there is no reason".
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
