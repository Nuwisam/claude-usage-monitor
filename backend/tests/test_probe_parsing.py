"""Tests of the client probe (client/usage-probe.py).

The probe is the only code in the project running on the critical path, and at
the same time it NEVER raises — so every defect in it is silent by definition. Hence the
tests here, even though this is not backend code: it is the only place anything checks it.

The file is loaded by path, because a name with a hyphen is not a valid module identifier,
and renaming it would break the existing hook configurations on machines.
"""
import importlib.util
import time
from pathlib import Path

import pytest

PROBE = Path(__file__).resolve().parents[2] / "client" / "usage-probe.py"


@pytest.fixture(scope="module")
def probe():
    spec = importlib.util.spec_from_file_location("usage_probe", PROBE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------- stdout parser
REAL_OUTPUT = """You are currently using your subscription to power your Claude Code usage

Current session: 48% used · resets Jul 27, 12:30pm (UTC)
Current week (all models): 47% used · resets Aug 1, 6pm (UTC)
Current week (Fable): 0% used
Current week (Sonnet only): 12% used

What's contributing to your limits usage?
Approximate, based on local sessions on this machine — does not include other devices.

Last 24h · 1104 requests · 5 sessions
  100% of your usage came from subagent-heavy sessions
  89% of your usage came from sessions active for 8+ hours
  Top subagents: Explore 5%, Plan 1%
"""


def test_parses_real_output(probe):
    r = probe.parse_usage_text(REAL_OUTPUT)
    assert r["session"] == 48
    assert r["weekly_all"] == 47
    assert r["scoped"] == {"Fable": 0, "Sonnet only": 12}


def test_ignores_attribution_section(probe):
    """The '100% of your usage came from...' lines carry a percentage but have no colon
    before it. Were they to match, 100% would land as a limit reading."""
    r = probe.parse_usage_text(REAL_OUTPUT)
    assert 100 not in (r["session"], r["weekly_all"])
    assert 100 not in r["scoped"].values()


@pytest.mark.parametrize("bad", ["", None, "something completely different",
                                 "Current session: missing"])
def test_garbage_does_not_crash_the_parser(probe, bad):
    r = probe.parse_usage_text(bad)
    assert r == {"session": None, "weekly_all": None, "scoped": {}}


def test_rejects_absurd_percentage(probe):
    """Defect #52326 in Claude Code can put the epoch from resets_at into the percentage
    field. Clamping to 100 would turn a failure into a believable false alarm, so it is
    rejected instead."""
    r = probe.parse_usage_text("Current session: 1785143542% used\n"
                               "Current week (all models): 47% used")
    assert r["session"] is None
    assert r["weekly_all"] == 47


# ---------------------------------------------------------------------------- merge
def _cache(five=40, weekly=41, resets="2099-01-01T00:00:00+00:00"):
    return {
        "five_hour": {"utilization": five, "resets_at": resets},
        "seven_day": {"utilization": weekly, "resets_at": resets},
        "extra_usage": {"is_enabled": False},
        "spend": {"percent": 0, "severity": "normal", "enabled": False},
        "limits": [
            {"kind": "session", "group": "session", "percent": five,
             "severity": "normal", "resets_at": resets, "is_active": True, "scope": None},
            {"kind": "weekly_all", "group": "weekly", "percent": weekly,
             "severity": "normal", "resets_at": resets, "is_active": False, "scope": None},
            {"kind": "weekly_scoped", "group": "weekly", "percent": 0,
             "severity": "normal", "resets_at": None, "is_active": False,
             "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None}},
        ],
    }


def test_merge_overwrites_with_fresh_percentages(probe):
    usage, covered = probe.merge(_cache(), {"session": 48, "weekly_all": 47,
                                            "scoped": {"Fable": 3}})
    assert usage["five_hour"]["utilization"] == 48
    assert usage["seven_day"]["utilization"] == 47
    by_kind = {l["kind"]: l for l in usage["limits"]}
    assert by_kind["session"]["percent"] == 48
    assert by_kind["weekly_all"]["percent"] == 47
    assert by_kind["weekly_scoped"]["percent"] == 3
    assert set(covered) == {"bucket:five_hour", "bucket:seven_day", "limit:session:-",
                            "limit:weekly_all:-", "limit:weekly_scoped:Fable"}


def test_coverage_is_not_a_value_change(probe):
    """A fresh reading EQUAL to the cached value is a confirmation, not a missing event.
    Previously only changes counted, so a series with an unchanged percentage and an expired
    window dropped out of the measurement instead of getting `reset-w-toku` — losing the one
    true reading. Dating on the backend side depends on that set as well."""
    _, covered = probe.merge(_cache(five=40, weekly=41),
                             {"session": 40, "weekly_all": 41, "scoped": {}})
    assert "bucket:five_hour" in covered and "bucket:seven_day" in covered


def test_merge_does_not_crash_on_odd_scope(probe):
    """`scope` as a string instead of a dictionary: `merge` runs before `log_local` and
    before the spool, so an exception here would wipe out the whole run without a trace —
    on every cycle, for as long as the cache has that shape."""
    c = _cache()
    c["limits"][2]["scope"] = "global"
    c["limits"][0]["scope"] = ["something", "completely", "different"]
    usage, covered = probe.merge(c, {"session": 48, "weekly_all": 47, "scoped": {"Fable": 3}})
    assert usage["limits"][0]["percent"] == 48
    assert "limit:weekly_scoped:-" not in covered, "without a model there is no percentage match"


def test_dump_older_than_cache_is_rejected(probe):
    """A dump may be up to 900 s old, and ordinary work refreshes the cache in that time —
    the order can invert. When a window reset falls between the dump and the cache, laying
    the dump over it gives a percentage from BEFORE the reset against a boundary from AFTER
    it: 95% against a window that really holds ~1%. `sanitize` does not see this, because
    the boundary is valid."""
    assert probe.dump_outdated(1000.0, 1120.0) is True
    assert probe.dump_outdated(1120.0, 1000.0) is False
    assert probe.dump_outdated(1000.0, 1000.0) is False, "equal age is not an inversion"
    # A missing stamp on either side is not proof of an inversion.
    assert probe.dump_outdated(None, 1000.0) is False
    assert probe.dump_outdated(1000.0, 0) is False


def test_merge_keeps_fields_stdout_does_not_have(probe):
    """This is the entire reason for two sources: stdout gives the percentages, but spend/
    extra_usage/severity/is_active exist only in the cache."""
    usage, _ = probe.merge(_cache(), {"session": 48, "weekly_all": 47, "scoped": {}})
    assert usage["spend"]["severity"] == "normal"
    assert usage["extra_usage"]["is_enabled"] is False
    assert usage["limits"][0]["is_active"] is True


def test_merge_without_fresh_data_returns_cache_unchanged(probe):
    src = _cache()
    usage, covered = probe.merge(src, None)
    assert usage == src and covered == []


def test_merge_does_not_mutate_the_source(probe):
    src = _cache()
    probe.merge(src, {"session": 99, "weekly_all": 99, "scoped": {}})
    assert src["five_hour"]["utilization"] == 40


# ------------------------------------------------------------------------- sanitize
PAST = "2020-01-01T00:00:00+00:00"


def test_expired_window_without_fresh_data_drops_out(probe):
    """A percentage from a window that has already reset is simply untrue — the real figure
    now is close to zero. Publishing the former 95% would be a gross error."""
    usage, _ = probe.merge(_cache(five=95, resets=PAST), None)
    usage, events = probe.sanitize(usage, [], time.time())
    assert usage["five_hour"] is None
    assert any("okno-wygaslo" in e for e in events)
    assert all(l["kind"] != "session" for l in usage["limits"])


def test_expired_window_with_fresh_data_keeps_percent_but_loses_reset_time(probe):
    """The fresh percentage is true; only the cached resets_at is stale. The time is zeroed
    instead of discarding a good measurement — the next cache write will supply a new one."""
    usage, covered = probe.merge(_cache(five=95, resets=PAST),
                                 {"session": 2, "weekly_all": 41, "scoped": {}})
    usage, events = probe.sanitize(usage, covered, time.time())
    assert usage["five_hour"]["utilization"] == 2
    assert usage["five_hour"]["resets_at"] is None
    assert any("reset-w-toku" in e for e in events)


def test_future_reset_is_left_untouched(probe):
    usage, covered = probe.merge(_cache(), {"session": 48, "weekly_all": 47, "scoped": {}})
    usage, events = probe.sanitize(usage, covered, time.time())
    assert usage["five_hour"]["resets_at"] == "2099-01-01T00:00:00+00:00"
    assert events == []
    assert len(usage["limits"]) == 3


def test_absurd_value_in_cache_drops_out(probe):
    usage, _ = probe.sanitize(_cache(five=1785143542), [], time.time())
    assert usage["five_hour"] is None


def test_fresh_percent_equal_to_cache_saves_series_with_expired_window(probe):
    """Regression: `sanitize` asks about COVERAGE, not about change. When the dump confirmed
    the same value while the window in the cache had already expired, the series must get
    `reset-w-toku` — it used to drop out of the measurement entirely, losing the one true
    reading."""
    usage, covered = probe.merge(_cache(five=40, resets=PAST),
                                 {"session": 40, "weekly_all": 41, "scoped": {}})
    usage, events = probe.sanitize(usage, covered, time.time())
    assert usage["five_hour"]["utilization"] == 40
    assert usage["five_hour"]["resets_at"] is None
    assert any("reset-w-toku" in e for e in events)


def test_sanitize_does_not_crash_on_odd_scope(probe):
    """`sanitize` also derives the limit's model, so the same shape must survive here too."""
    c = _cache(resets=PAST)
    c["limits"][2]["scope"] = "global"
    usage, covered = probe.merge(c, {"session": 2, "weekly_all": 41, "scoped": {}})
    usage, events = probe.sanitize(usage, covered, time.time())
    assert usage["five_hour"]["utilization"] == 2
    assert events


def test_sanitize_tolerates_missing_resets_at(probe):
    usage, events = probe.sanitize(_cache(resets=None), [], time.time())
    assert usage["five_hour"]["utilization"] == 40
    assert events == []


# ------------------------------------------------ reading ~/.claude.json (family B)
# `_extract_block` and `read_claude_json` had NOT ONE test until now, even though the manual
# slicing that replaced `json.load` was built around them. The fixtures are complete, real
# files from a Team account in three credit states — with identities scrubbed, but with the
# case-differing duplicate key and the real size preserved, because `_extract_block` walks
# the text with `text.find`.
def test_credits_disabled_reason_distinguishes_three_states(probe):
    """The only field that on its own tells an exhausted OWN pool from the organization
    ceiling: in `spend.disabled_reason` an exhausted pool leaves it `null`."""
    from tests.team import CLAUDE_JSON_STATES

    for path, expected in CLAUDE_JSON_STATES:
        text = path.read_text("utf-8")
        assert probe._extract_scalar(text, "cachedExtraUsageDisabledReason") == expected, \
            path.name


def test_extract_scalar_does_not_raise_on_corrupted_file(probe):
    """Rule 3: the probe never raises. Every input must yield a value or None."""
    import json

    from tests.team import CLAUDE_JSON_COMPANY_EXHAUSTED

    KEY = "cachedExtraUsageDisabledReason"
    full_text = CLAUDE_JSON_COMPANY_EXHAUSTED.read_text("utf-8")
    truncated_in_value = full_text[: full_text.find(KEY) + len(KEY) + 8]
    for text in ("", "{", '{"other": 1}', full_text[: full_text.find(KEY)], truncated_in_value,
                 full_text[: len(full_text) // 2]):
        assert probe._safe(probe._extract_scalar, text, KEY) is None

    # A truncated TAIL of the file no longer spoils the value, and that is the whole advantage of
    # the manual extraction over `json.load`, which returns NOTHING on a truncated file.
    assert probe._extract_scalar(full_text[:-3], KEY) == "org_level_disabled_until"
    assert probe._safe(json.loads, full_text[:-3]) is None

    # Non-string values must get through without an exception too — the contract is open.
    for raw, expected in (('{"k": null}', None), ('{"k": 7}', 7),
                          ('{"k": true}', True), ('{"k":   "x"}', "x"),
                          ('{"k": "a\\"b"}', 'a"b')):
        assert probe._extract_scalar(raw, "k") == expected


def test_extract_block_matches_full_parse(probe):
    """The manual slicing must return EXACTLY what a parser of the whole file would give —
    otherwise the probe sends something other than what Claude Code wrote. It also pins down
    the behavior on the case-differing duplicate key that these files really carry."""
    import json

    from tests.team import CLAUDE_JSON_STATES

    for path, _ in CLAUDE_JSON_STATES:
        text = path.read_text("utf-8")
        parsed = json.loads(text)
        for key in ("oauthAccount", "cachedUsageUtilization"):
            assert probe._extract_block(text, key) == parsed[key], "%s / %s" % (
                path.name, key)

        keys = [k for k in parsed["projects"]]
        assert any(k.lower() == j.lower() and k != j for k in keys for j in keys), \
            "%s lost the duplicate key — fixture no longer tests what it was built for" % path.name


def test_read_claude_json_returns_account_measurement_and_reason_for_each_state(probe, monkeypatch):
    """The whole read path together, on three real states. A missing file is not an error —
    on a fresh machine Claude Code has not written it yet."""
    from tests.team import CLAUDE_JSON_STATES

    for path, expected in CLAUDE_JSON_STATES:
        monkeypatch.setattr(probe, "_find", lambda name, in_claude_dir=False, p=path: str(p))
        acct, cached, cfg_dir, reason = probe.read_claude_json()

        assert acct["emailAddress"] == "usage-monitor@example.test"
        assert acct["organizationType"] == "claude_team"
        assert isinstance(cached["utilization"]["spend"], dict)
        # Rule 7: ingest keys by accountUuid and both sides must agree.
        assert cached["accountUuid"] == acct["accountUuid"]
        assert reason == expected

    monkeypatch.setattr(probe, "_find", lambda name, in_claude_dir=False: None)
    assert probe.read_claude_json() == (None, None, None, None)
