"""Tests of the client probe (client/usage-probe.py).

The probe is the only code in the project running on the critical path, and at
the same time it NEVER raises — so every defect in it is silent by definition. Hence the
tests here, even though this is not backend code: it is the only place anything checks it.

The file is loaded by path, because a name with a hyphen is not a valid module identifier,
and renaming it would break the existing hook configurations on machines.
"""
import importlib.util
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tests.iso_cases import CASES, DEPENDS, READS, REFUSES

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
def _at(hours=0, days=0, tz="+00:00"):
    """A boundary relative to NOW, rendered with the offset asked for.

    Fixed future literals rot: `_cache` used to default to `2099-01-01`, which the
    `MAX_AHEAD_S` ceiling now reads as garbage — correctly. Fixed PAST literals do not rot
    the same way, so `PAST` below stays a literal.

    CALL THIS ONCE and bind the result. Two calls return two different strings (microsecond
    `isoformat()`), so rendering it again inside an assertion gives an intermittently red
    test whose failure looks like a probe defect.
    """
    d = datetime.now(timezone.utc) + timedelta(hours=hours, days=days)
    if tz != "+00:00":
        sign = 1 if tz[0] == "+" else -1
        d = d.astimezone(timezone(sign * timedelta(hours=int(tz[1:3]), minutes=int(tz[4:6]))))
    return d.isoformat()


_DEFAULT = object()          # `resets=None` means "no boundary", a real and tested state


def _cache(five=40, weekly=41, resets=_DEFAULT):
    if resets is _DEFAULT:
        resets = _at(hours=3)
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
    window dropped out of the measurement instead of getting `reset-in-progress` — losing the one
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
    src = _cache(five=95, resets=PAST)
    usage, _ = probe.merge(src, None)
    usage, events = probe.sanitize(usage, [], time.time())
    assert usage["five_hour"] is None
    assert any("window-expired" in e for e in events)
    assert all(l["kind"] != "session" for l in usage["limits"])
    # main() ships THIS object as `usage_raw` (:1847) — sanitize must not have reached it.
    assert src == _cache(five=95, resets=PAST), "the cache block must survive sanitize"


def test_expired_window_with_fresh_data_keeps_percent_but_loses_reset_time(probe):
    """The fresh percentage is true; only the cached resets_at is stale. The time is zeroed
    instead of discarding a good measurement — the next cache write will supply a new one."""
    usage, covered = probe.merge(_cache(five=95, resets=PAST),
                                 {"session": 2, "weekly_all": 41, "scoped": {}})
    usage, events = probe.sanitize(usage, covered, time.time())
    assert usage["five_hour"]["utilization"] == 2
    assert usage["five_hour"]["resets_at"] is None
    assert any("reset-in-progress" in e for e in events)


def test_future_reset_is_left_untouched(probe):
    boundary = _at(hours=3)          # ONE call, bound — see `_at`
    usage, covered = probe.merge(_cache(resets=boundary),
                                 {"session": 48, "weekly_all": 47, "scoped": {}})
    usage, events = probe.sanitize(usage, covered, time.time())
    assert usage["five_hour"]["resets_at"] == boundary
    assert events == []
    assert len(usage["limits"]) == 3


def test_absurd_value_in_cache_drops_out(probe):
    usage, _ = probe.sanitize(_cache(five=1785143542), [], time.time())
    assert usage["five_hour"] is None


def test_fresh_percent_equal_to_cache_saves_series_with_expired_window(probe):
    """Regression: `sanitize` asks about COVERAGE, not about change. When the dump confirmed
    the same value while the window in the cache had already expired, the series must get
    `reset-in-progress` — it used to drop out of the measurement entirely, losing the one true
    reading."""
    usage, covered = probe.merge(_cache(five=40, resets=PAST),
                                 {"session": 40, "weekly_all": 41, "scoped": {}})
    usage, events = probe.sanitize(usage, covered, time.time())
    assert usage["five_hour"]["utilization"] == 40
    assert usage["five_hour"]["resets_at"] is None
    assert any("reset-in-progress" in e for e in events)


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


# --------------------------------------------------- sanitize: the boundary ceiling
# `exp <= now` is a one-sided test. A boundary implausibly far ahead passed it unchallenged
# and reached `series_state`, where `carry_reset_window` holds an unexpired boundary as true
# — so a stamp from 2099 became the state's boundary, and with it the countdown, the delta
# baseline's window and `inferred_reset` were wrong for as long as it stayed.
def test_boundary_far_in_the_future_is_zeroed_and_the_series_stays(probe):
    usage, events = probe.sanitize(_cache(resets=_at(days=401)), [], time.time())
    assert usage["five_hour"]["utilization"] == 40, "the percentage is not implicated"
    assert usage["five_hour"]["resets_at"] is None
    assert "five_hour:window-from-future" in events


def test_boundary_far_in_the_future_keeps_the_limit_row(probe):
    """The neighbouring branch ends in `continue`; this one must NOT. Dropping the row would
    look downstream exactly like Anthropic withdrawing the limit."""
    usage, events = probe.sanitize(_cache(resets=_at(days=401)), [], time.time())
    assert len(usage["limits"]) == 3
    session = [l for l in usage["limits"] if l["kind"] == "session"][0]
    assert session["percent"] == 40 and session["resets_at"] is None
    assert "limit:session:window-from-future" in events


def test_the_ceiling_is_a_ceiling_and_not_a_slope(probe):
    """Passes without the guard too — it pins where the ceiling sits against a later
    retune, it does not prove the guard was built."""
    boundary = _at(days=399)
    usage, events = probe.sanitize(_cache(resets=boundary), [], time.time())
    assert usage["five_hour"]["resets_at"] == boundary
    assert events == []


def test_the_ceiling_reads_the_true_instant_not_the_substring(probe):
    """The case that needs the merged `_epoch`, and the reason it straddles the ceiling.

    A boundary 400 d + 2 h ahead written at `-05:00`: the true instant is over the ceiling,
    while `iso[:19]` read as UTC lands 400 d − 3 h out, under it. The obvious "401 days at
    -05:00" proves nothing — five hours cannot flip a one-day margin, so it trips under the
    offset-discarding parser as well."""
    usage, events = probe.sanitize(_cache(resets=_at(days=400, hours=2, tz="-05:00")),
                                   [], time.time())
    assert usage["five_hour"]["resets_at"] is None
    assert "five_hour:window-from-future" in events


# ------------------------------------------------------------------- `_epoch`, one body
def test_the_probe_defines_no_name_twice(probe):
    """The defect was a SECOND `def _epoch` 585 lines below the first: Python bound the
    second, `sanitize` silently ran it, and 25 tests stayed green because every observed
    `resets_at` carries `+00:00`, where the two bodies agreed. This is the class, not the
    instance — a re-bound module-level constant shadows exactly as quietly."""
    import ast
    tree = ast.parse(PROBE.read_text(encoding="utf-8"))
    names = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names.extend(t.id for t in node.targets if isinstance(t, ast.Name))
    dupes = sorted({n for n in names if names.count(n) > 1})
    assert dupes == [], "shadowed at module level: %s" % dupes


@pytest.mark.parametrize("case", [c for c in CASES if c.probe == READS],
                         ids=lambda c: c.label)
def test_epoch_reads_the_shared_cases(probe, case):
    got = probe._epoch(case.raw)
    assert got is not None, case.why
    assert abs(got - case.instant.timestamp()) < 1e-6, case.why


@pytest.mark.parametrize("case", [c for c in CASES if c.probe == REFUSES],
                         ids=lambda c: c.label)
def test_epoch_refuses_the_shared_cases(probe, case):
    """Refusing is not a defect — the value still reaches the backend verbatim in
    `usage_raw`, and `parse_ts` reads several of these. What must never happen is a wrong
    instant, which is what the offset-discarding body did."""
    assert probe._epoch(case.raw) is None, case.why


@pytest.mark.parametrize("case", [c for c in CASES if c.probe == DEPENDS],
                         ids=lambda c: c.label)
def test_epoch_is_never_wrong_even_where_the_interpreter_decides(probe, case):
    got = probe._epoch(case.raw)
    if got is not None:
        assert abs(got - case.instant.timestamp()) < 1e-6, case.why


def test_epoch_keeps_the_fraction_the_signaller_orders_by(probe):
    """`since` has one-second resolution, the transcript milliseconds. Truncating here would
    invert the order — and a lexicographic comparison inverts it too, because '.' < 'Z'."""
    since = probe._epoch("2026-08-11T13:53:28Z")
    later = probe._epoch("2026-08-11T13:53:28.588Z")
    assert later > since
    assert "2026-08-11T13:53:28.588Z" < "2026-08-11T13:53:28Z", "why not a string compare"


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


# --------------------------------------------------------------------------- spool
def test_backlog_batch_fits_under_the_ingest_body_cap(probe, tmp_path, monkeypatch):
    """MAX_BACKLOG_PER_REQUEST is a COUNT, and 200 full records are ~1.03 MB — over the
    backend's body cap, which is checked BEFORE the body is parsed. The 413 that comes back
    is indistinguishable here from any other failure: the record is spooled and the very
    same oldest window is rebuilt on the next cycle, forever. Hence the byte budget."""
    import json

    line = json.dumps({"usage": {}, "usage_raw": {"pad": "x" * 5000}}, ensure_ascii=False)
    spool = tmp_path / "spool.jsonl"
    spool.write_text("\n".join([line] * 400) + "\n", encoding="utf-8")
    monkeypatch.setattr(probe, "SPOOL", str(spool))

    record = {"usage": {}, "usage_raw": {"pad": "y" * 5000}}
    budget = probe.MAX_REQUEST_BYTES - len(
        json.dumps(record, ensure_ascii=False).encode("utf-8"))
    backlog, total = probe.read_spool(probe.MAX_BACKLOG_PER_REQUEST, budget)

    assert total == 400                     # the whole spool is still reported, unshrunk
    assert backlog                          # ...and the batch is not empty either
    assert backlog == [json.loads(line)] * len(backlog)      # a prefix, in file order

    body = json.dumps(dict(record, backlog=backlog), ensure_ascii=False).encode("utf-8")
    assert len(body) <= probe.MAX_REQUEST_BYTES               # what the 413 measures


def test_backlog_is_empty_when_the_oldest_entry_alone_does_not_fit(probe, tmp_path,
                                                                   monkeypatch):
    """`trim_spool` trims by POSITION, so the batch has to stay a prefix: an entry skipped
    in the middle would be dropped from the spool without ever being sent. An entry that
    does not fit therefore ends the batch — and when it is the first one, the live record
    travels alone rather than dragging the whole cycle into a 413."""
    import json

    spool = tmp_path / "spool.jsonl"
    spool.write_text(json.dumps({"pad": "x" * 300000}) + "\n"
                     + json.dumps({"pad": "small"}) + "\n", encoding="utf-8")
    monkeypatch.setattr(probe, "SPOOL", str(spool))

    assert probe.read_spool(probe.MAX_BACKLOG_PER_REQUEST, 200000) == ([], 2)
