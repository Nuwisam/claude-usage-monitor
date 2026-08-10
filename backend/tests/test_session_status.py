"""Tests for the blocked-session signaller — the "alert" section of client/usage-probe.py.

Here rather than in `client/`, for the same reason as `test_probe_parsing.py`: this is code
running in the working path that NEVER raises — meaning every one of its bugs is silent by
definition, and this is the only place where anything checks it at all.

The signaller was a separate script until measurement showed that folding it into the probe
process costs 2.7 ms against 41.9 ms for a separate process. The tests stayed; only the file
they are loaded from changed.

Every case matches measured harness behavior, not an idea of it. The measurement methodology
lives in the script's own docstring.

i18n-keep: four things in this file stay Polish and none of them is untranslated work.
This module is the only surviving record of a cp1250 bug, and the record is made of the
bytes themselves:

  * the mojibake `'umieraÄ‡'` and the note that `Ł` is `C5 81` — what the failure LOOKED
    like. Retype either and the evidence is gone;
  * three docstrings that stay Polish whole, because they are the sentences explaining
    those bytes;
  * four `Z:\\projects\\...` literals in the `cwd` fixtures. A Windows drive path is what
    the hook really receives, and the cp1250 crash happened on exactly such a path;
  * the Polish file path and shell command fed as tool input. The accented characters are
    the input under test — replace them with ASCII and the test still passes while
    exercising nothing.

Verify by bytes, never by console: a terminal rendering these as garbage is its codepage,
not corruption. Four separate reviewers reported that false alarm.
"""
import importlib.util
import io
import json
import os
import shutil
import time
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "client" / "usage-probe.py"


@pytest.fixture
def ss(tmp_path, monkeypatch):
    """A fresh module with its state directory in tmp_path. The module, not an instance,
    because the paths are module constants in it — exactly as the hook sees them."""
    spec = importlib.util.spec_from_file_location("usage_probe_alert", SRC)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.OUTDIR = str(tmp_path)
    mod.STATEDIR = str(tmp_path / "session-status")
    mod.POSTED = str(tmp_path / "posted.txt")
    mod.CONFIG = str(tmp_path / "config.json")
    # The harness session registry goes to tmp_path too. By default the directory DOES NOT
    # EXIST, so the set of live sessions is "unknown" and the death rule does not run — tests
    # that do not concern it take the same path as before.
    mod.REGDIR = str(tmp_path / "sessions")
    # The probe paths go to tmp_path as well: `main()` is called here and must not touch
    # the real machine state or fire `claude -p`.
    mod.THROTTLE_FILE = str(tmp_path / "last-probe.txt")
    mod.LOG = str(tmp_path / "usage-samples.jsonl")
    mod.SPOOL = str(tmp_path / "spool.jsonl")
    mod.CLI_OUT = str(tmp_path / "usage-cli.json")
    # No toasts, no network, no child processes in tests.
    monkeypatch.setattr(mod, "toast", lambda *a, **kw: None)
    monkeypatch.setattr(mod, "spawn_refresh", lambda cfg: None)
    mod.sent = []
    monkeypatch.setattr(mod, "post",
                        lambda cfg, url, body: (mod.sent.append(body), (200, "{}"))[1])
    return mod


CFG = {"alert_url": "https://example.org/api/session-alert", "ingest_token": "t"}

SID = "session-1"


def hook(event, **kw):
    base = {"hook_event_name": event, "session_id": SID,
            "cwd": r"Z:\projects\claude-usage-monitor",
            "transcript_path": str(Path.home() / ".claude" / "projects"
                                   / "z--projects-claude-usage-monitor" / "x.jsonl"),
            "prompt_id": "p1"}
    base.update(kw)
    return base


def names(ss):
    return sorted(n for n, _ in ss.entries())


def seed_registry(ss, *session_ids):
    """`<pid>.json` records in the substituted harness registry.

    Filled in BEFORE `alert_dispatch`: `registry_seen` is written in `enter()`, and `O_EXCL`
    will not let it be corrected afterwards."""
    os.makedirs(ss.REGDIR, exist_ok=True)
    for i, sid in enumerate(session_ids):
        pid = 1000 + i
        with open(os.path.join(ss.REGDIR, "%d.json" % pid), "w", encoding="utf-8") as f:
            json.dump({"pid": pid, "sessionId": sid, "kind": "interactive",
                       "cwd": r"Z:\projects\x", "version": "2.1.223"}, f)


# ---------------------------------------------------------------------- state machine
def test_permission_request_creates_entry(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "git status"}))
    assert len(names(ss)) == 1
    entry = ss.snapshot()[0]
    assert entry["reason"] == "permission"
    assert entry["detail"] == "git status"
    assert entry["project"] == "claude-usage-monitor"


def test_pretooluse_for_ordinary_tool_does_nothing(ss):
    """Hot path. `PermissionRequest` fires ONLY on a real question put to the human
    (measured: Read/Grep/Write/echo — zero occurrences), so `PreToolUse` has nothing
    to do here and must not touch the disk."""
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="Read",
                          tool_input={"file_path": "a.py"}, tool_use_id="toolu_1"))
    assert names(ss) == []
    assert not os.path.isdir(ss.STATEDIR)


@pytest.mark.parametrize("tool,reason", [("AskUserQuestion", "question"),
                                         ("ExitPlanMode", "plan")])
def test_two_tools_enter_via_pretooluse(ss, tool, reason):
    """These two ALWAYS block, so `PreToolUse` produces no false positives for them —
    and it carries the `tool_use_id` that `PermissionRequest` does not have."""
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name=tool, tool_input={},
                          tool_use_id="toolu_9"))
    assert ss.snapshot()[0]["reason"] == reason
    assert names(ss) == ["%s__main__toolu_9.json" % SID]


def test_permission_request_does_not_duplicate_entry_for_those_two(ss):
    """The order of `PreToolUse` vs `PermissionRequest` is NOT GUARANTEED (measured
    20% inversions), so two entry sources for one call would give a race over two
    files and two toasts."""
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="AskUserQuestion", tool_input={},
                          tool_use_id="toolu_9"))
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="AskUserQuestion",
                          tool_input={}))
    assert len(names(ss)) == 1


def test_posttooluse_closes_by_call_key(ss):
    """`PermissionRequest` has no `tool_use_id`, so the entry is keyed on `call_key`.
    The exit computes BOTH candidates and deletes both — not knowing which mode made it."""
    ti = {"command": "git status"}
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash", tool_input=ti))
    ss.alert_dispatch(CFG, hook("PostToolUse", tool_name="Bash", tool_input=ti,
                          tool_use_id="toolu_1", tool_response="ok"))
    assert names(ss) == []


def test_posttooluse_closes_by_tool_use_id_despite_changed_tool_input(ss):
    """Measured: the harness merges answers into AskUserQuestion's `tool_input` between
    entry and exit (1326 -> 1649 B). A uniform hash would drift apart for exactly the
    tool it was supposed to be most reliable for."""
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="AskUserQuestion",
                          tool_input={"questions": [{"question": "which option?"}]},
                          tool_use_id="toolu_7"))
    ss.alert_dispatch(CFG, hook("PostToolUse", tool_name="AskUserQuestion",
                          tool_input={"questions": [{"question": "which option?"}],
                                      "answers": {"a": "b"}},
                          tool_use_id="toolu_7"))
    assert names(ss) == []


def test_posttoolbatch_closes_what_posttooluse_does_not(ss):
    """Measured: Edit on a plan file, 0/6 closed by `PostToolUse`,
    and 4/4 covered by `tool_calls[]`. This is not redundancy."""
    ti = {"file_path": "plan.md"}
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Edit", tool_input=ti))
    ss.alert_dispatch(CFG, hook("PostToolBatch", tool_calls=[
        {"tool_name": "Edit", "tool_input": ti, "tool_use_id": "toolu_2"}]))
    assert names(ss) == []


def test_event_with_agent_id_does_not_close_main_thread_entry(ss):
    """The one failure mode this function does not tolerate is a false UNBLOCK.

    Measured: of 393 block windows, 8 had foreign tool calls during them, 155 events,
    154 of them from subagents — up to 52 'exit'-class events during a single
    six-minute dialog. This is a steady state, not a race.
    """
    ti = {"command": "git status"}
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash", tool_input=ti))
    ss.alert_dispatch(CFG, hook("PostToolUse", tool_name="Bash", tool_input=ti,
                          tool_use_id="toolu_1", agent_id="agent-a"))
    assert len(names(ss)) == 1, "subagent closed the main thread's block"


def test_subagent_has_its_own_entry(ss):
    """Subagents share the parent's `session_id` and are told apart by `agent_id` —
    confirmed on 161,295/161,295 sidechain records and measured live (76 events)."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "ls"}, agent_id="agent-a",
                          agent_type="general-purpose"))
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "ls"}))
    assert len(names(ss)) == 2


# ----------------------------------------------------------------------- sweeping
@pytest.mark.parametrize("event", ["UserPromptSubmit", "Stop", "SessionEnd"])
def test_sweep_clears_alert_after_refusal(ss, event):
    """Nothing that ends a call other than by normal execution generates ANY event —
    a refusal by button, Esc at the prompt and Esc mid-run each gave silence 5/5
    times. Prefix sweeping is the only mechanism that clears them."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "rm -rf /"}))
    ss.alert_dispatch(CFG, hook(event))
    assert names(ss) == []


def test_sweep_does_not_touch_another_sessions_alert(ss):
    """`SessionEnd` arrives ~once a minute with the id of the `claude -p` child the
    probe fires. A global sweep would wipe the alerts every minute.

    The registry is substituted explicitly and both sessions ARE in it — otherwise the case
    would pass only because the set of live sessions is unknown, and would say nothing."""
    seed_registry(ss, SID, "child-claude-p")
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "ls"}))
    ss.alert_dispatch(CFG, hook("SessionEnd", session_id="child-claude-p"))
    assert len(names(ss)) == 1


# ------------------------------------------ entry death from the registry
# The harness registry (`~/.claude/sessions/<pid>.json`) is the only source of knowledge on
# whether a session is still alive. Never `os.kill` — on Windows that is `TerminateProcess`.
#
# The sweeping event has a DIFFERENT `session_id` than the entry and is NOT
# `SessionStart`/`SessionEnd` (the rule does not run on those two), or the case takes
# a different path.
def foreign_session_entry(ss, sid="foreign-session", in_registry=True):
    """An entry for session `sid`, created while that session WAS in the registry (so with
    `registry_seen` set)."""
    if in_registry:
        seed_registry(ss, sid, SID)
    else:
        seed_registry(ss, SID)
    ss.alert_dispatch(CFG, hook("PermissionRequest", session_id=sid, tool_name="Bash",
                                tool_input={"command": "ls"}))
    return names(ss)[0]


def kill_registry_record(ss, sid):
    """Removes this session's record from the registry — as the harness does on process exit.

    The handle is closed BEFORE `os.remove`: Windows will not delete an open file
    (WinError 32) — the same pitfall `drop()` works around with three attempts."""
    to_remove = []
    for f in os.listdir(ss.REGDIR):
        p = os.path.join(ss.REGDIR, f)
        with open(p, encoding="utf-8") as fh:
            if json.load(fh).get("sessionId") == sid:
                to_remove.append(p)
    for p in to_remove:
        os.remove(p)


def test_entry_for_session_without_record_dies_and_is_published(ss):
    """A closed tab fires no hook at all, but its registry record disappears (measured
    <=5 s on window close, 626 ms after killing the process — the harness removes it)."""
    entry_name = foreign_session_entry(ss)
    kill_registry_record(ss, "foreign-session")
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [], entry_name
    assert ss.sent[-1]["entries"] == [], "the set becoming empty MUST reach the panel"


def test_entry_for_session_with_record_stays(ss):
    """Negative control: the human is still waiting in that tab."""
    foreign_session_entry(ss)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert len(names(ss)) == 1


def test_entry_without_registry_seen_is_exempt_from_rule(ss):
    """A session the harness does not register really exists: a console-less `claude.exe`
    lived 18 s and never got a record. Without this marker its block would die instantly."""
    foreign_session_entry(ss, in_registry=False)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert len(names(ss)) == 1
    assert ss.read_entry(names(ss)[0])["registry_seen"] is False


def test_missing_registry_directory_deletes_nothing(ss):
    """"Unknown" NEVER means "empty" — otherwise a machine with no registry would clear
    all of its own alerts on the first event."""
    entry_name = foreign_session_entry(ss)
    shutil.rmtree(ss.REGDIR)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [entry_name]


def test_one_unreadable_record_invalidates_the_whole_pass(ss):
    """A half-read directory would shorten the live set and delete other entries WHOLESALE."""
    entry_name = foreign_session_entry(ss)
    kill_registry_record(ss, "foreign-session")
    os.mkdir(os.path.join(ss.REGDIR, "31337.json"))     # name matches, `open` raises
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [entry_name]


def test_files_that_are_not_records_are_ignored(ss):
    """The harness also keeps `.in_use` and `.last_inuse_sweep` in this directory — it
    filters on `<digits>.json` and we filter the same way. These are not "unparsable
    records"."""
    entry_name = foreign_session_entry(ss)
    kill_registry_record(ss, "foreign-session")
    for junk_name in (".in_use", ".last_inuse_sweep", "not-a-number.json"):
        with open(os.path.join(ss.REGDIR, junk_name), "w", encoding="utf-8") as f:
            f.write("whatever, not json")
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [], entry_name


def test_entry_name_without_three_parts_survives(ss):
    """`garbage.json` really can be in there (see the snapshot test). A name outside the
    scheme is not dead — it is foreign, so we leave it alone."""
    seed_registry(ss, SID)
    os.makedirs(ss.STATEDIR, exist_ok=True)
    with open(os.path.join(ss.STATEDIR, "garbage.json"), "w", encoding="utf-8") as f:
        json.dump({"registry_seen": True, "reason": "permission"}, f)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == ["garbage.json"]


@pytest.mark.parametrize("event", ["SessionStart", "SessionEnd"])
def test_rule_does_not_run_at_session_boundaries(ss, event):
    """Measured: on `SessionStart` the record appears 0.2-1.0 s AFTER the first hook (13/13),
    and on `SessionEnd` it still EXISTS (14/14). On those two its absence proves nothing.

    The sweeping session MUST be in the registry (`hook()` uses `SID`, which we put there),
    or the "no current session" safeguard saves the entry and the case says nothing about the
    boundaries.
    """
    entry_name = foreign_session_entry(ss)
    kill_registry_record(ss, "foreign-session")
    ss.alert_dispatch(CFG, hook(event))
    assert names(ss) == [entry_name]
    # Positive control: the same event that is not a boundary DOES delete the entry.
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == []


def test_current_session_missing_from_registry_holds_off_rule(ss):
    """The session this hook runs in is ALIVE. If it is not in the registry, then we do not
    understand the registry — and nothing may be deleted on its authority."""
    entry_name = foreign_session_entry(ss)
    kill_registry_record(ss, "foreign-session")
    kill_registry_record(ss, SID)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [entry_name]


def test_holding_off_leaves_a_trace_in_the_log(ss):
    """Otherwise "the mechanism died after a change at Anthropic" and "there is nothing to
    collect" are indistinguishable, and the symptom is exactly the bug this section fixes."""
    foreign_session_entry(ss)
    shutil.rmtree(ss.REGDIR)
    ss.alert_dispatch(CFG, hook("Stop"))
    with open(ss.LOG, encoding="utf-8") as f:
        lines = [json.loads(l) for l in f if l.strip()]
    assert [r for r in lines if r.get("alert_skip") == "rejestr-niepelny"]


def test_empty_state_directory_does_not_read_registry(ss, monkeypatch):
    """Hot path: with an empty state directory there is nothing to collect, so the registry
    must not be opened. Measured to be this branch's entire cost on a typical event."""
    monkeypatch.setattr(ss, "live_sessions",
                        lambda: pytest.fail("registry read while state directory was empty"))
    ss.alert_dispatch(CFG, hook("Stop"))
    ss.alert_dispatch(CFG, hook("UserPromptSubmit"))


def test_ttl_deletes_but_never_hides(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "ls"}))
    entry_name = names(ss)[0]
    old_ts = time.time() - 2 * ss.DEFAULT_TTL_S
    os.utime(os.path.join(ss.STATEDIR, entry_name), (old_ts, old_ts))
    ss.sweep_ttl(ss.DEFAULT_TTL_S, time.time())
    assert names(ss) == []


# ------------------------------------------------- closing from the transcript
# A refusal and Esc generate NO hook event, but they DO WRITE a `tool_result` into the
# transcript — measured on 2.1.223 three times, with three different bodies. This is the only
# path that clears the alert of a session gone quiet after a refusal, and it works from the
# sweep of ANY session.
#
# In every case the sweeping event has a DIFFERENT `session_id` than the entry. Without that
# the entry dies from prefix sweeping before anyone opens the transcript, and the test says
# nothing.
@pytest.fixture
def tdir(tmp_path):
    """The transcript directory. The name MUST stay the project slug, because `project` is
    computed from it and the other tests' assertions rely on that."""
    d = tmp_path / "z--projects-claude-usage-monitor"
    d.mkdir()
    return d


def tool_use_record(tool, ti, tuid):
    """An `assistant` record with a `tool_use` block — measured to occur only there."""
    return {"type": "assistant", "timestamp": "2026-08-07T10:00:00.000Z",
            "message": {"content": [{"type": "tool_use", "id": tuid,
                                     "name": tool, "input": ti}]}}


def tool_result_record(tuid, when, prompt_id="p1", is_error=True):
    """A `user` record with a `tool_result` block. `promptId` is on THIS record and only here."""
    return {"type": "user", "timestamp": when, "promptId": prompt_id,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tuid,
                                     "is_error": is_error,
                                     "content": "The user doesn't want to proceed"}]}}


def write_transcript(path, *records):
    """A transcript from records; a `str` passes through raw so a broken line can be put in."""
    lines = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
             for r in records]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def later(ss):
    return ss._iso(time.time() + 5)


def sweep_other_session(ss):
    ss.alert_dispatch(CFG, hook("Stop", session_id="other-session"))


def plan_entry(ss, tp, tuid="toolu_9"):
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="ExitPlanMode", tool_input={},
                                tool_use_id=tuid, transcript_path=tp))


def permission_entry(ss, tp, ti, **kw):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash", tool_input=ti,
                                transcript_path=tp, **kw))


def test_transcript_clears_plan_after_refusal_from_anothers_sweep(ss, tdir):
    """The reported case: a plan rejected, then the session went quiet. Today such an
    entry has no collector, because `Stop` does not fire on an interrupted turn."""
    tp = write_transcript(tdir / "x.jsonl", tool_use_record("ExitPlanMode", {}, "toolu_9"),
                tool_result_record("toolu_9", later(ss)))
    plan_entry(ss, tp)
    assert len(names(ss)) == 1
    sweep_other_session(ss)
    assert names(ss) == []
    assert ss.sent[-1]["entries"] == [], "the set becoming empty MUST reach the panel"


def test_call_alone_without_result_does_not_clear(ss, tdir):
    """Negative control on a live case: the human is STILL waiting."""
    tp = write_transcript(tdir / "x.jsonl", tool_use_record("ExitPlanMode", {}, "toolu_9"))
    plan_entry(ss, tp)
    sweep_other_session(ss)
    assert len(names(ss)) == 1


def test_key_quoted_as_free_text_does_not_clear(ss, tdir):
    """Substring matching would clear EVERY live block: the key is always in the tail,
    because it sits in its own `tool_use` record."""
    tp = write_transcript(tdir / "x.jsonl", tool_use_record("ExitPlanMode", {}, "toolu_9"),
                {"type": "assistant", "timestamp": "2026-08-07T10:01:00.000Z",
                 "message": {"content": [{"type": "text",
                                          "text": "I see toolu_9 in the log, check it"}]}})
    plan_entry(ss, tp)
    sweep_other_session(ss)
    assert len(names(ss)) == 1


def test_missing_transcript_file_does_not_clear(ss, tdir):
    plan_entry(ss, str(tdir / "does-not-exist.jsonl"))
    sweep_other_session(ss)
    assert len(names(ss)) == 1


def test_broken_line_mid_tail_does_not_lose_the_result(ss, tdir):
    """`_safe` on each line separately: one truncated line must not eat the rest of the tail."""
    tp = write_transcript(tdir / "x.jsonl", tool_use_record("ExitPlanMode", {}, "toolu_9"),
                '{"type": "user", "message": {"content": [{"type": "tool_re',
                tool_result_record("toolu_9", later(ss)))
    plan_entry(ss, tp)
    sweep_other_session(ss)
    assert names(ss) == []


def test_unreadable_transcript_of_one_entry_does_not_block_another(ss, tdir):
    """`_safe` around the whole read of one file — otherwise a single blocked transcript
    would hang every other block on the machine."""
    broken_path = tdir / "directory-not-file.jsonl"
    broken_path.mkdir()                                    # open() on a directory raises
    ok = write_transcript(tdir / "x.jsonl", tool_use_record("ExitPlanMode", {}, "toolu_b"),
                tool_result_record("toolu_b", later(ss)))
    plan_entry(ss, str(broken_path), tuid="toolu_a")
    plan_entry(ss, ok, tuid="toolu_b")
    sweep_other_session(ss)
    assert names(ss) == ["%s__main__toolu_a.json" % SID]


def test_transcript_shorter_than_tail_does_not_lose_first_line(ss, tdir):
    """The first line is incomplete ONLY when the read really started in mid-file."""
    tp = write_transcript(tdir / "x.jsonl", tool_result_record("toolu_9", later(ss)))
    plan_entry(ss, tp)
    sweep_other_session(ss)
    assert names(ss) == []


def test_sent_entry_does_not_carry_local_fields(ss, tdir):
    """`transcript_path` carries the human's home directory name and has no recipient
    in `SessionAlert`; neither does `prompt_id`. On disk they must stay."""
    tp = str(tdir / "x.jsonl")
    permission_entry(ss, tp, {"command": "ls"})
    sent_entry = ss.sent[-1]["entries"][0]
    assert "transcript_path" not in sent_entry and "prompt_id" not in sent_entry
    on_disk = ss.read_entry(names(ss)[0])
    assert on_disk["transcript_path"] == tp and on_disk["prompt_id"] == "p1"


# --- `permission` entries: the key is a hash, so the id must be recovered by recomputing
def test_permission_closes_by_recomputing_call_key(ss, tdir):
    ti = {"command": "git push --force"}
    tp = write_transcript(tdir / "x.jsonl", tool_use_record("Bash", ti, "toolu_x"),
                tool_result_record("toolu_x", later(ss)))
    permission_entry(ss, tp, ti)
    entry_name = names(ss)[0]
    assert "toolu_x" not in entry_name, \
        "the entry's key must be a hash, otherwise the case takes the question/plan rule's path"
    sweep_other_session(ss)
    assert names(ss) == []


def test_same_tool_with_different_input_does_not_clear(ss, tdir):
    ti = {"command": "git push --force"}
    tp = write_transcript(tdir / "x.jsonl", tool_use_record("Bash", {"command": "git status"}, "toolu_x"),
                tool_result_record("toolu_x", later(ss)))
    permission_entry(ss, tp, ti)
    sweep_other_session(ss)
    assert len(names(ss)) == 1


@pytest.mark.parametrize("is_error", [True, False])
def test_identical_retry_without_result_does_not_clear(ss, tdir, is_error):
    """Measured: 0.24% of calls have a byte-identical twin inside a 32 KB window, and the
    "refused, Claude repeats the identical call" scenario occurred 22 times in the corpus.
    Both have the same `call_key`, so ONLY the last `tool_use` record counts."""
    ti = {"command": "git push --force"}
    tp = write_transcript(tdir / "x.jsonl", tool_use_record("Bash", ti, "toolu_1"),
                tool_result_record("toolu_1", later(ss), is_error=is_error),
                tool_use_record("Bash", ti, "toolu_2"))
    permission_entry(ss, tp, ti)
    sweep_other_session(ss)
    assert len(names(ss)) == 1, "cleared a block the human has not seen yet"


def test_positive_control_when_younger_retry_is_also_resolved(ss, tdir):
    """The same state with the safeguard not met — without this the test above passes also
    with the mechanism cut out."""
    ti = {"command": "git push --force"}
    tp = write_transcript(tdir / "x.jsonl", tool_use_record("Bash", ti, "toolu_1"),
                tool_result_record("toolu_1", later(ss)), tool_use_record("Bash", ti, "toolu_2"),
                tool_result_record("toolu_2", later(ss)))
    permission_entry(ss, tp, ti)
    sweep_other_session(ss)
    assert names(ss) == []


def test_result_from_another_turn_does_not_clear(ss, tdir):
    """`prompt_id` spans the human's whole turn (measured 5-12 calls, 89-199 s)."""
    ti = {"command": "ls"}
    tp = write_transcript(tdir / "x.jsonl", tool_use_record("Bash", ti, "toolu_x"),
                tool_result_record("toolu_x", later(ss), prompt_id="p2"))
    permission_entry(ss, tp, ti)
    sweep_other_session(ss)
    assert len(names(ss)) == 1


def test_result_older_than_block_entry_does_not_clear(ss, tdir):
    """Closes a retry in the SAME turn that `promptId` does not catch. Comparison on
    parsed time: lexicographically '...:40.816Z' < '...:40Z', because '.' < 'Z'."""
    ti = {"command": "ls"}
    tp = write_transcript(tdir / "x.jsonl", tool_use_record("Bash", ti, "toolu_x"),
                tool_result_record("toolu_x", ss._iso(time.time() - 300)))
    permission_entry(ss, tp, ti)
    sweep_other_session(ss)
    assert len(names(ss)) == 1


def test_entry_without_prompt_id_is_not_closed(ss, tdir):
    """An older probe. A hash over an empty string cannot tell turns apart, so leave it be."""
    ti = {"command": "ls"}
    tp = write_transcript(tdir / "x.jsonl", tool_use_record("Bash", ti, "toolu_x"),
                tool_result_record("toolu_x", later(ss), prompt_id=None))
    permission_entry(ss, tp, ti, prompt_id=None)
    sweep_other_session(ss)
    assert len(names(ss)) == 1


# --- subagent: the hook carries the PARENT's `transcript_path`, records live in a separate file
def test_subagent_entry_closes_from_its_own_file(ss, tdir):
    parent_tp = write_transcript(tdir / "x.jsonl", tool_use_record("ExitPlanMode", {}, "toolu_9"))
    write_transcript(tdir / SID / "subagents" / "agent-a1d9fb.jsonl",
           tool_use_record("ExitPlanMode", {}, "toolu_9"), tool_result_record("toolu_9", later(ss)))
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="ExitPlanMode", tool_input={},
                                tool_use_id="toolu_9", transcript_path=parent_tp,
                                agent_id="a1d9fb", agent_type="general-purpose"))
    assert len(names(ss)) == 1
    sweep_other_session(ss)
    assert names(ss) == [], "the subagent's file was not derived from `agent_id`"


def test_missing_subagent_file_does_not_fall_back_to_parent(ss, tdir):
    """The parent has a resolution, but not of THIS call — the subagent has its own file."""
    parent_tp = write_transcript(tdir / "x.jsonl", tool_use_record("ExitPlanMode", {}, "toolu_9"),
                    tool_result_record("toolu_9", later(ss)))
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="ExitPlanMode", tool_input={},
                                tool_use_id="toolu_9", transcript_path=parent_tp,
                                agent_id="a1d9fb"))
    sweep_other_session(ss)
    assert len(names(ss)) == 1


# ----------------------------------------------------------------- writing and sending
def test_o_excl_preserves_since(ss):
    """Re-entering the same block must not move the stamp — otherwise
    'waiting 40 min' would reset on every twitch."""
    h = hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"})
    ss.alert_dispatch(CFG, h)
    since = ss.snapshot()[0]["since"]
    time.sleep(1.05)
    ss.alert_dispatch(CFG, h)
    assert ss.snapshot()[0]["since"] == since


def test_post_only_on_set_change(ss):
    h = hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"})
    ss.alert_dispatch(CFG, h)
    assert len(ss.sent) == 1
    ss.alert_dispatch(CFG, h)                     # the same block, set unchanged
    assert len(ss.sent) == 1
    ss.alert_dispatch(CFG, hook("Stop"))
    assert len(ss.sent) == 2
    assert ss.sent[-1]["entries"] == [], "the set becoming empty MUST reach the panel"


def test_manual_file_deletion_reaches_the_panel(ss):
    """The emergency hatch from the RUNBOOK: `del %LOCALAPPDATA%\\...\\session-status\\*`. The
    deletion comes from OUTSIDE the probe, so nothing published it and the panel kept a card
    for a block that is gone. The sweep now compares the set against the marker
    unconditionally."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    assert len(ss.sent) == 1 and ss.sent[-1]["entries"]
    for n in names(ss):                              # this is what `del` does
        os.remove(os.path.join(ss.STATEDIR, n))
    ss.alert_dispatch(CFG, hook("Stop", session_id="other-session"))
    assert ss.sent[-1]["entries"] == [], "the panel would be left with a stale card"


def test_ttl_expiry_reaches_the_panel(ss):
    """`sweep_ttl` deletes and never publishes — the same root cause as the `del` hatch.
    Symptom: two days later the block is long gone and the triangle is still hanging."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    entry_name = names(ss)[0]
    old_ts = time.time() - 2 * ss.DEFAULT_TTL_S
    os.utime(os.path.join(ss.STATEDIR, entry_name), (old_ts, old_ts))
    ss.alert_dispatch(CFG, hook("Stop", session_id="other-session"))
    assert names(ss) == []
    assert ss.sent[-1]["entries"] == []


def test_expiry_of_one_of_two_entries_reaches_the_panel(ss):
    """The same failure with a NON-EMPTY directory: TTL takes one entry of two, in that
    run the probe deletes nothing, and the server keeps both. That is why reconciliation
    must not sit behind `if hit`."""
    for cmd in ("ls", "pwd"):
        ss.alert_dispatch(CFG, hook("PermissionRequest", session_id="session-%s" % cmd,
                                    tool_name="Bash", tool_input={"command": cmd}))
    seed_registry(ss, "session-ls", "session-pwd", SID)       # both sessions ARE ALIVE, so none is swept
    assert len(ss.sent[-1]["entries"]) == 2
    old_ts = time.time() - 2 * ss.DEFAULT_TTL_S
    p = os.path.join(ss.STATEDIR, names(ss)[0])
    os.utime(p, (old_ts, old_ts))
    ss.alert_dispatch(CFG, hook("Stop"))
    assert len(names(ss)) == 1
    assert len(ss.sent[-1]["entries"]) == 1, "the server would keep an entry that no longer exists"


def test_unchanged_set_generates_no_posts(ss):
    """An unconditional `publish()` must not mean "send on every event" — the marker
    decides that. Otherwise an idle machine would talk to the server every turn."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    ile = len(ss.sent)
    for _ in range(5):
        ss.alert_dispatch(CFG, hook("Stop", session_id="other-session"))
    assert len(ss.sent) == ile


def test_empty_directory_and_empty_marker_stay_silent(ss):
    """The commonest run on a machine with no block: nothing to reconcile and nothing flies."""
    for _ in range(5):
        ss.alert_dispatch(CFG, hook("Stop"))
        ss.alert_dispatch(CFG, hook("UserPromptSubmit"))
    assert ss.sent == []


def test_hot_path_does_not_reconcile(ss):
    """`PostToolUse` fires on EVERY tool call. Reconciling there would mean reading the
    directory and the marker on the densest event this probe ever sees."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    for n in names(ss):
        os.remove(os.path.join(ss.STATEDIR, n))
    ile = len(ss.sent)
    ss.alert_dispatch(CFG, hook("PostToolUse", tool_name="Read", tool_input={},
                                tool_use_id="toolu_x"))
    assert len(ss.sent) == ile, "the closing branch started talking to the server"


def test_failed_post_does_not_write_marker(ss, monkeypatch):
    monkeypatch.setattr(ss, "post", lambda *a, **kw: None)
    h = hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"})
    ss.alert_dispatch(CFG, h)
    assert not os.path.exists(ss.POSTED), "otherwise the loss would never repeat"


def test_local_mode_without_configuration(ss):
    """Without `alert_url` the script writes files and raises a toast, sends nothing. That
    is a legal state, not a failure — and the fallback channel when the server is down."""
    ss.alert_dispatch({}, hook("PermissionRequest", tool_name="Bash",
                         tool_input={"command": "ls"}))
    assert len(names(ss)) == 1
    assert ss.sent == []


def test_polish_characters_in_detail(ss, tmp_path):
    """Bez jawnego utf-8 cp1250 wywala sie na polskiej sciezce — i to jest wyjatek
    w skrypcie, ktory ma prawa nie rzucac."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Edit",
                          tool_input={"file_path": r"C:\Zażółć\gęślą\jaźń.py"}))
    assert "jaźń" in ss.snapshot()[0]["detail"]


def test_detail_is_truncated(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "x" * 500}))
    assert len(ss.snapshot()[0]["detail"]) == ss.DETAIL_MAX


# ----------------------------------------------------------------------- project name
def test_worktree_gives_project_name_not_agent_name(ss):
    """`basename(cwd)` would give 'agent-a00ce9ba287d12ab1', and a walk-up to `.git` would
    stop at the worktree, because there `.git` is a FILE, not a directory."""
    transcript = str(Path.home() / ".claude" / "projects"
                     / "z--projects-claude-usage-monitor" / "x.jsonl")
    assert ss.project_name(r"Z:\projects\claude-usage-monitor\.claude\worktrees"
                           r"\agent-a00ce9ba287d12ab1",
                           transcript) == "claude-usage-monitor"


def test_subdirectory_does_not_become_project_name(ss):
    """Measured: 38 of 73 sessions report more than one `cwd`. The header would show
    'src' instead of the project name."""
    transcript = str(Path.home() / ".claude" / "projects"
                     / "z--projects-claude-usage-monitor" / "x.jsonl")
    assert ss.project_name(r"Z:\projects\claude-usage-monitor\frontend\src",
                           transcript) == "claude-usage-monitor"


def test_drive_letter_case_mismatch_does_not_break_matching(ss):
    """~/.claude.json REALLY does hold duplicate keys differing only in the case of the
    drive letter — the same drift affects `cwd`."""
    transcript = str(Path.home() / ".claude" / "projects"
                     / "z--projects-claude-usage-monitor" / "x.jsonl")
    assert ss.project_name(r"z:\Projects\Claude-Usage-Monitor",
                           transcript) == "Claude-Usage-Monitor"


def test_missing_transcript_falls_back_to_walk_up(ss, tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "repo" / "sub").mkdir()
    assert ss.project_name(str(tmp_path / "repo" / "sub"), None) == "repo"


def test_project_name_never_raises(ss):
    for cwd in (None, "", 42, r"C:\\"):
        ss.project_name(cwd, None)


# --------------------------------------------------------------------- robustness
def test_garbage_input_does_not_raise(ss):
    for h in ({}, {"hook_event_name": "SomethingNew"},
              {"hook_event_name": "PermissionRequest"},
              {"hook_event_name": "PostToolUse", "session_id": None},
              {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"}):
        ss.alert_dispatch(CFG, h)


def test_corrupted_state_file_does_not_break_snapshot(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "ls"}))
    with open(os.path.join(ss.STATEDIR, "garbage.json"), "w", encoding="utf-8") as f:
        f.write("{this is not json")
    assert len(ss.snapshot()) == 1


def test_call_key_is_stable_and_depends_on_prompt(ss):
    ti = {"command": "git status"}
    assert ss.call_key("Bash", ti, "p1") == ss.call_key("Bash", ti, "p1")
    assert ss.call_key("Bash", ti, "p1") != ss.call_key("Bash", ti, "p2")
    assert ss.call_key("Bash", ti, "p1") != ss.call_key("Edit", ti, "p1")


def test_guard_against_recursion(ss, monkeypatch):
    """The probe's `claude -p "/usage"` is an ordinary Claude Code session and fires the
    same hooks. The throttle alone will not stop it — every child has its own clock."""
    monkeypatch.setenv(ss.CHILD_ENV, "1")
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(
        hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"}))))
    assert ss.main() == 0
    assert names(ss) == []


# ---------------------------------------------------------------------- flag
def test_flag_disables_everything(ss):
    ss.alert_dispatch(dict(CFG, session_status=False),
                      hook("PermissionRequest", tool_name="Bash",
                           tool_input={"command": "ls"}))
    assert names(ss) == []
    assert ss.sent == []


def test_missing_key_means_enabled(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    assert len(names(ss)) == 1


def test_disabling_clears_whats_pending(ss):
    """A bare `return` would not be enough: a block in progress at the moment of switching
    off would stay on the panel until the server TTL, because nobody would send a correction."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    assert len(names(ss)) == 1 and len(ss.sent) == 1
    ss.alert_dispatch(dict(CFG, session_status=False), hook("PostToolUse"))
    assert names(ss) == []
    assert ss.sent[-1]["entries"] == [], "the panel must receive the empty set"


def test_disabled_with_empty_directory_does_not_talk_to_server(ss):
    """Otherwise a switched-off feature would send a POST on EVERY hook event."""
    for _ in range(5):
        ss.alert_dispatch(dict(CFG, session_status=False), hook("PostToolUse"))
    assert ss.sent == []


def test_disabled_does_not_reconcile_even_on_drift(ss):
    """Reconciling the set against the marker MUST NOT bypass the switch. The marker ends up
    out of step with the disk, but a switched-off feature has to keep quiet — cleanup happens
    by switching it back on, or by hand."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    for n in names(ss):
        os.remove(os.path.join(ss.STATEDIR, n))     # drift: disk empty, marker not
    ile = len(ss.sent)
    for event in ("Stop", "UserPromptSubmit", "SessionEnd", "SessionStart"):
        ss.alert_dispatch(dict(CFG, session_status=False), hook(event))
    assert len(ss.sent) == ile


def test_local_mode_does_not_reach_network_on_reconcile(ss):
    """Without `alert_url` the signaller works locally only. Reconciliation computes the
    snapshot and reads the marker, but must not send anything."""
    local_cfg = {"toast": False}
    ss.alert_dispatch(local_cfg, hook("PermissionRequest", tool_name="Bash",
                                    tool_input={"command": "ls"}))
    assert len(names(ss)) == 1 and ss.sent == []
    for n in names(ss):
        os.remove(os.path.join(ss.STATEDIR, n))
    for event in ("Stop", "UserPromptSubmit", "SessionStart"):
        ss.alert_dispatch(local_cfg, hook(event))
    assert ss.sent == []
    assert not os.path.exists(ss.POSTED), "local mode must not write the marker"


def test_custom_ttl_from_config_is_respected(ss):
    """`blocked_ttl_sec` from `config.json` still works, and its effect now reaches the panel."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    entry_name = names(ss)[0]
    old_ts = time.time() - 120
    os.utime(os.path.join(ss.STATEDIR, entry_name), (old_ts, old_ts))
    ss.alert_dispatch(dict(CFG, blocked_ttl_sec=3600), hook("Stop", session_id="other"))
    assert names(ss) == [entry_name], "the entry died before its own TTL"
    ss.alert_dispatch(dict(CFG, blocked_ttl_sec=60), hook("Stop", session_id="other"))
    assert names(ss) == []
    assert ss.sent[-1]["entries"] == []


# --------------------------------------------------------------- merged into the probe
def _run_probe(ss, monkeypatch, payload, throttle_fresh=True):
    """The probe's full `main()` with a substituted stdin."""
    with open(ss.CONFIG, "w", encoding="utf-8") as f:
        json.dump({"alert_url": CFG["alert_url"], "ingest_token": "t",
                   "throttle_sec": 3600}, f)
    if throttle_fresh:
        with open(ss.THROTTLE_FILE, "w") as f:
            f.write(str(time.time()))
    # `ensure_ascii=False` is LOAD-BEARING here, not cosmetic: the default escaping would
    # give a pure-ASCII payload, so no decoder would have anything to corrupt and the
    # encoding test would always pass. Claude Code sends raw characters.
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(payload, ensure_ascii=False)))
    return ss.main()


def test_alert_fires_before_throttle(ss, monkeypatch):
    """A regression on the ordering inside `main()`. The probe throttle is 60 s; were the
    alert to run after it, a block would be visible only after a minute or — on dense
    events — not at all."""
    assert _run_probe(ss, monkeypatch,
                    hook("PermissionRequest", tool_name="Bash",
                         tool_input={"command": "git push --force"})) == 0
    assert len(names(ss)) == 1, "the probe's throttle ate the alert"


def test_probe_is_silent_on_stdout_for_permission_request(ss, monkeypatch, capsys):
    """`PermissionRequest` is a DECISION hook: anything on stdout changes the prompt's
    behavior. The contract reads 'exit 0 with no JSON = leave the decision to the human'."""
    _run_probe(ss, monkeypatch, hook("PermissionRequest", tool_name="Bash",
                                   tool_input={"command": "ls"}))
    captured = capsys.readouterr()
    assert captured.out == ""


def test_recursion_guard_cuts_alert_too(ss, monkeypatch):
    monkeypatch.setenv(ss.CHILD_ENV, "1")
    assert _run_probe(ss, monkeypatch,
                    hook("PermissionRequest", tool_name="Bash",
                         tool_input={"command": "ls"})) == 0
    assert names(ss) == []


def test_hook_stdin_is_read_as_utf8(ss, monkeypatch):
    """Payload hooka to UTF-8, ale `sys.stdin` w trybie tekstowym dekodowal go
    kodowaniem locale — na ekranie i w toascie wychodzilo 'umieraÄ‡' zamiast
    'umierac' z ogonkami. Asercja idzie przez `snapshot()`, bo on ma jawne utf-8;
    gole `open()` przeczytaloby poprawny plik jako cp1250 i dalo czerwien na dobrym
    kodzie."""
    assert _run_probe(ss, monkeypatch,
                    hook("PermissionRequest", tool_name="Bash",
                         tool_input={"command": "echo zażółć gęślą jaźń"})) == 0
    assert "zażółć gęślą jaźń" in ss.snapshot()[0]["detail"]


def test_stdin_with_byte_outside_cp1250_does_not_lose_alert(ss, monkeypatch):
    """Twardszy tryb tej samej awarii, i to on boli bardziej. cp1250 nie ma
    odpowiednika dla 0x81/0x83/0x88/0x90/0x98, a `sys.stdin` ma
    `errors=surrogateescape` — wiec 'Ł' (C5 81) nie psul sie na ekranie, tylko
    stawal sie samotnym surogatem. Ten wywracal `write_excl` na `.encode("utf-8")`
    ("surrogates not allowed"), a tam stoi `except Exception: pass`: plik wpisu
    powstawal PUSTY. Skutek: toast leci, `read_entry` nie parsuje, `snapshot()`
    pomija, alert nigdy nie dociera na panel — a klucz jest juz zajety, wiec
    ponowienie tez nic nie da."""
    assert _run_probe(ss, monkeypatch,
                    hook("PermissionRequest", tool_name="Edit",
                         tool_input={"file_path": r"C:\Łukasz\gęś.py"})) == 0
    assert len(ss.snapshot()) == 1
    assert "Łukasz" in ss.snapshot()[0]["detail"]


class _Stdin:
    """The double must decode BADLY, or there is nothing to catch.

    The real `sys.stdin` in a hook process gets UTF-8 bytes and decodes them with
    the locale encoding (cp1250 on the machine where this was measured) — `.read()`
    reproduces exactly that, while `.buffer` carries the truth. A double that simply returned a fixed `str` was a convenient fiction: it passed the same before the fix
    and after it.

    `surrogateescape`, not `strict`: measured on a child process started the way
    Claude Code starts hooks — `sys.stdin.errors` is precisely
    `surrogateescape`. So bytes with no cp1250 equivalent do not raise right away,
    they turn into lone surrogates that only topple `.encode()` a layer further
    on. A double using `strict` would be harsher than reality and would send the
    search for the failure to the wrong place.
    """

    def __init__(self, text):
        self.raw = text.encode("utf-8")
        self.buffer = io.BytesIO(self.raw)

    def read(self):
        return self.raw.decode("cp1250", "surrogateescape")     # like the real stdin
