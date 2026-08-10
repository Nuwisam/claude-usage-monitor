"""Tests for the blocked-session signaller — the "alert" section of client/usage-probe.py.

Here rather than in `client/`, for the same reason as `test_probe_parsing.py`: this is code
running in the working path that NEVER raises — meaning every one of its bugs is silent by
definition, and this is the only place where anything checks it at all.

The signaller was a separate script until measurement showed that folding it into the probe
process costs 2.7 ms against 41.9 ms for a separate process. The tests stayed; only the file
they are loaded from changed.

Every case matches measured harness behavior, not an idea of it. The measurement methodology
lives in the script's own docstring.
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
    mod.wyslane = []
    monkeypatch.setattr(mod, "post",
                        lambda cfg, url, body: (mod.wyslane.append(body), (200, "{}"))[1])
    return mod


CFG = {"alert_url": "https://example.org/api/session-alert", "ingest_token": "t"}

SID = "sesja-1"


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


def rejestr(ss, *session_ids):
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
def test_permission_request_zaklada_wpis(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "git status"}))
    assert len(names(ss)) == 1
    wpis = ss.snapshot()[0]
    assert wpis["reason"] == "permission"
    assert wpis["detail"] == "git status"
    assert wpis["project"] == "claude-usage-monitor"


def test_pretooluse_zwyklego_narzedzia_nie_robi_nic(ss):
    """Hot path. `PermissionRequest` fires ONLY on a real question put to the human
    (measured: Read/Grep/Write/echo — zero occurrences), so `PreToolUse` has nothing
    to do here and must not touch the disk."""
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="Read",
                          tool_input={"file_path": "a.py"}, tool_use_id="toolu_1"))
    assert names(ss) == []
    assert not os.path.isdir(ss.STATEDIR)


@pytest.mark.parametrize("tool,reason", [("AskUserQuestion", "question"),
                                         ("ExitPlanMode", "plan")])
def test_dwa_narzedzia_wchodza_przez_pretooluse(ss, tool, reason):
    """These two ALWAYS block, so `PreToolUse` produces no false positives for them —
    and it carries the `tool_use_id` that `PermissionRequest` does not have."""
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name=tool, tool_input={},
                          tool_use_id="toolu_9"))
    assert ss.snapshot()[0]["reason"] == reason
    assert names(ss) == ["%s__main__toolu_9.json" % SID]


def test_permission_request_nie_dubluje_wejscia_tych_dwoch(ss):
    """The order of `PreToolUse` vs `PermissionRequest` is NOT GUARANTEED (measured
    20% inversions), so two entry sources for one call would give a race over two
    files and two toasts."""
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="AskUserQuestion", tool_input={},
                          tool_use_id="toolu_9"))
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="AskUserQuestion",
                          tool_input={}))
    assert len(names(ss)) == 1


def test_posttooluse_zamyka_po_call_key(ss):
    """`PermissionRequest` has no `tool_use_id`, so the entry stands on `call_key`.
    The exit computes BOTH candidates and deletes both — not knowing which mode made it."""
    ti = {"command": "git status"}
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash", tool_input=ti))
    ss.alert_dispatch(CFG, hook("PostToolUse", tool_name="Bash", tool_input=ti,
                          tool_use_id="toolu_1", tool_response="ok"))
    assert names(ss) == []


def test_posttooluse_zamyka_po_tool_use_id_mimo_zmienionego_tool_input(ss):
    """Measured: the harness merges answers into AskUserQuestion's `tool_input` between
    entry and exit (1326 -> 1649 B). A uniform hash would drift apart for exactly the
    tool it was supposed to be most reliable for."""
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="AskUserQuestion",
                          tool_input={"questions": [{"question": "ktory wariant?"}]},
                          tool_use_id="toolu_7"))
    ss.alert_dispatch(CFG, hook("PostToolUse", tool_name="AskUserQuestion",
                          tool_input={"questions": [{"question": "ktory wariant?"}],
                                      "answers": {"a": "b"}},
                          tool_use_id="toolu_7"))
    assert names(ss) == []


def test_posttoolbatch_domyka_to_czego_posttooluse_nie(ss):
    """Measured: Edit on a plan file, 0/6 closed by `PostToolUse`,
    and 4/4 covered by `tool_calls[]`. This is not redundancy."""
    ti = {"file_path": "plan.md"}
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Edit", tool_input=ti))
    ss.alert_dispatch(CFG, hook("PostToolBatch", tool_calls=[
        {"tool_name": "Edit", "tool_input": ti, "tool_use_id": "toolu_2"}]))
    assert names(ss) == []


def test_zdarzenie_z_agent_id_nie_zamyka_wpisu_watku_glownego(ss):
    """The one failure mode this function does not tolerate is a false UNBLOCK.

    Measured: of 393 block windows, 8 had foreign tool calls during them, 155 events,
    154 of them from subagents — up to 52 'exit'-class events during a single
    six-minute dialog. This is a steady state, not a race.
    """
    ti = {"command": "git status"}
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash", tool_input=ti))
    ss.alert_dispatch(CFG, hook("PostToolUse", tool_name="Bash", tool_input=ti,
                          tool_use_id="toolu_1", agent_id="agent-a"))
    assert len(names(ss)) == 1, "subagent zamknal blokade watku glownego"


def test_subagent_ma_wlasny_wpis(ss):
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
def test_zamiatanie_gasi_alert_po_odmowie(ss, event):
    """Nothing that ends a call other than by normal execution generates ANY event —
    a refusal by button, Esc at the prompt and Esc mid-run each gave silence 5/5
    times. Prefix sweeping is the only mechanism that clears them."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "rm -rf /"}))
    ss.alert_dispatch(CFG, hook(event))
    assert names(ss) == []


def test_zamiatanie_nie_rusza_cudzej_sesji(ss):
    """`SessionEnd` arrives ~once a minute with the id of the `claude -p` child the
    probe fires. A global sweep would wipe the alerts every minute.

    The registry is substituted explicitly and both sessions ARE in it — otherwise the case
    would pass only because the set of live sessions is unknown, and would say nothing."""
    rejestr(ss, SID, "dziecko-claude-p")
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "ls"}))
    ss.alert_dispatch(CFG, hook("SessionEnd", session_id="dziecko-claude-p"))
    assert len(names(ss)) == 1


# ------------------------------------------ entry death from the registry
# The harness registry (`~/.claude/sessions/<pid>.json`) is the only source of knowledge on
# whether a session is still alive. Never `os.kill` — on Windows that is `TerminateProcess`.
#
# The sweeping event has a DIFFERENT `session_id` than the entry and is NOT
# `SessionStart`/`SessionEnd` (the rule does not run on those two), or the case takes
# a different path.
def wpis_obcej_sesji(ss, sid="obca-sesja", w_rejestrze=True):
    """An entry for session `sid`, created while that session WAS in the registry (so with
    `registry_seen` set)."""
    if w_rejestrze:
        rejestr(ss, sid, SID)
    else:
        rejestr(ss, SID)
    ss.alert_dispatch(CFG, hook("PermissionRequest", session_id=sid, tool_name="Bash",
                                tool_input={"command": "ls"}))
    return names(ss)[0]


def zabij_rekord(ss, sid):
    """Removes this session's record from the registry — as the harness does on process exit.

    The handle is closed BEFORE `os.remove`: Windows will not delete an open file
    (WinError 32) — the same pitfall `drop()` works around with three attempts."""
    do_usuniecia = []
    for f in os.listdir(ss.REGDIR):
        p = os.path.join(ss.REGDIR, f)
        with open(p, encoding="utf-8") as fh:
            if json.load(fh).get("sessionId") == sid:
                do_usuniecia.append(p)
    for p in do_usuniecia:
        os.remove(p)


def test_wpis_sesji_bez_rekordu_ginie_i_jest_publikowany(ss):
    """A closed tab fires no hook at all, but its registry record disappears (measured
    <=5 s on window close, 626 ms after killing the process — the harness removes it)."""
    nazwa = wpis_obcej_sesji(ss)
    zabij_rekord(ss, "obca-sesja")
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [], nazwa
    assert ss.wyslane[-1]["entries"] == [], "opustoszenie zbioru MUSI dojsc do panelu"


def test_wpis_sesji_z_rekordem_zostaje(ss):
    """Negative control: the human is still waiting in that tab."""
    wpis_obcej_sesji(ss)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert len(names(ss)) == 1


def test_wpis_bez_registry_seen_nie_podlega_regule(ss):
    """A session the harness does not register really exists: a console-less `claude.exe`
    lived 18 s and never got a record. Without this marker its block would die instantly."""
    wpis_obcej_sesji(ss, w_rejestrze=False)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert len(names(ss)) == 1
    assert ss.read_entry(names(ss)[0])["registry_seen"] is False


def test_brak_katalogu_rejestru_nie_kasuje_nic(ss):
    """"Unknown" NEVER means "empty" — otherwise a machine with no registry would clear
    all of its own alerts on the first event."""
    nazwa = wpis_obcej_sesji(ss)
    shutil.rmtree(ss.REGDIR)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [nazwa]


def test_jeden_nieczytelny_rekord_uniewaznia_caly_przebieg(ss):
    """A half-read directory would shorten the live set and delete other entries WHOLESALE."""
    nazwa = wpis_obcej_sesji(ss)
    zabij_rekord(ss, "obca-sesja")
    os.mkdir(os.path.join(ss.REGDIR, "31337.json"))     # name matches, `open` raises
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [nazwa]


def test_pliki_nie_bedace_rekordami_sa_ignorowane(ss):
    """The harness also keeps `.in_use` and `.last_inuse_sweep` in this directory — it
    filters on `<digits>.json` and we filter the same way. These are not "unparsable
    records"."""
    nazwa = wpis_obcej_sesji(ss)
    zabij_rekord(ss, "obca-sesja")
    for smiec in (".in_use", ".last_inuse_sweep", "nie-liczba.json"):
        with open(os.path.join(ss.REGDIR, smiec), "w", encoding="utf-8") as f:
            f.write("cokolwiek, nie JSON")
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [], nazwa


def test_nazwa_wpisu_bez_trzech_czlonow_nie_ginie(ss):
    """`smieci.json` really can be in there (see the snapshot test). A name outside the
    scheme is not dead — it is foreign, so we leave it alone."""
    rejestr(ss, SID)
    os.makedirs(ss.STATEDIR, exist_ok=True)
    with open(os.path.join(ss.STATEDIR, "smieci.json"), "w", encoding="utf-8") as f:
        json.dump({"registry_seen": True, "reason": "permission"}, f)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == ["smieci.json"]


@pytest.mark.parametrize("event", ["SessionStart", "SessionEnd"])
def test_na_brzegach_sesji_regula_nie_biegnie(ss, event):
    """Measured: on `SessionStart` the record appears 0.2-1.0 s AFTER the first hook (13/13),
    and on `SessionEnd` it still EXISTS (14/14). On those two its absence proves nothing.

    The sweeping session MUST be in the registry (`hook()` uses `SID`, which we put there),
    or the "no current session" safeguard saves the entry and the case says nothing about the
    boundaries.
    """
    nazwa = wpis_obcej_sesji(ss)
    zabij_rekord(ss, "obca-sesja")
    ss.alert_dispatch(CFG, hook(event))
    assert names(ss) == [nazwa]
    # Positive control: the same event that is not a boundary DOES delete the entry.
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == []


def test_brak_biezacej_sesji_w_rejestrze_wstrzymuje_regule(ss):
    """The session this hook runs in is ALIVE. If it is not in the registry, then we do not
    understand the registry — and nothing may be deleted on its authority."""
    nazwa = wpis_obcej_sesji(ss)
    zabij_rekord(ss, "obca-sesja")
    zabij_rekord(ss, SID)
    ss.alert_dispatch(CFG, hook("Stop"))
    assert names(ss) == [nazwa]


def test_wstrzymanie_zostawia_slad_w_logu(ss):
    """Otherwise "the mechanism died after a change at Anthropic" and "there is nothing to
    collect" are indistinguishable, and the symptom is exactly the bug this section fixes."""
    wpis_obcej_sesji(ss)
    shutil.rmtree(ss.REGDIR)
    ss.alert_dispatch(CFG, hook("Stop"))
    with open(ss.LOG, encoding="utf-8") as f:
        linie = [json.loads(l) for l in f if l.strip()]
    assert [r for r in linie if r.get("alert_skip") == "rejestr-niepelny"]


def test_pusty_katalog_stanu_nie_czyta_rejestru(ss, monkeypatch):
    """Hot path: with an empty state directory there is nothing to collect, so the registry
    must not be opened. Measured to be this branch's entire cost on a typical event."""
    monkeypatch.setattr(ss, "live_sessions",
                        lambda: pytest.fail("rejestr czytany przy pustym katalogu stanu"))
    ss.alert_dispatch(CFG, hook("Stop"))
    ss.alert_dispatch(CFG, hook("UserPromptSubmit"))


def test_ttl_kasuje_ale_nigdy_nie_ukrywa(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "ls"}))
    nazwa = names(ss)[0]
    stary = time.time() - 2 * ss.DEFAULT_TTL_S
    os.utime(os.path.join(ss.STATEDIR, nazwa), (stary, stary))
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
    computed from it and the other tests' assertions stand on that."""
    d = tmp_path / "z--projects-claude-usage-monitor"
    d.mkdir()
    return d


def uzycie(tool, ti, tuid):
    """An `assistant` record with a `tool_use` block — measured to occur only there."""
    return {"type": "assistant", "timestamp": "2026-08-07T10:00:00.000Z",
            "message": {"content": [{"type": "tool_use", "id": tuid,
                                     "name": tool, "input": ti}]}}


def wynik(tuid, kiedy, prompt_id="p1", is_error=True):
    """A `user` record with a `tool_result` block. `promptId` is on THIS record and only here."""
    return {"type": "user", "timestamp": kiedy, "promptId": prompt_id,
            "message": {"content": [{"type": "tool_result", "tool_use_id": tuid,
                                     "is_error": is_error,
                                     "content": "The user doesn't want to proceed"}]}}


def zapisz(path, *rekordy):
    """A transcript from records; a `str` passes through raw so a broken line can be put in."""
    linie = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
             for r in rekordy]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(linie) + "\n", encoding="utf-8")
    return str(path)


def pozniej(ss):
    return ss._iso(time.time() + 5)


def zamiataj(ss):
    ss.alert_dispatch(CFG, hook("Stop", session_id="inna-sesja"))


def wpis_plan(ss, tp, tuid="toolu_9"):
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="ExitPlanMode", tool_input={},
                                tool_use_id=tuid, transcript_path=tp))


def wpis_permission(ss, tp, ti, **kw):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash", tool_input=ti,
                                transcript_path=tp, **kw))


def test_transkrypt_gasi_plan_po_odmowie_z_cudzego_zamiatania(ss, tdir):
    """The reported case: a plan rejected, then the session went quiet. Today such an
    entry has no collector, because `Stop` does not fire on an interrupted turn."""
    tp = zapisz(tdir / "x.jsonl", uzycie("ExitPlanMode", {}, "toolu_9"),
                wynik("toolu_9", pozniej(ss)))
    wpis_plan(ss, tp)
    assert len(names(ss)) == 1
    zamiataj(ss)
    assert names(ss) == []
    assert ss.wyslane[-1]["entries"] == [], "opustoszenie zbioru MUSI dojsc do panelu"


def test_samo_wywolanie_bez_wyniku_nie_gasi(ss, tdir):
    """Negative control on a live case: the human is STILL waiting."""
    tp = zapisz(tdir / "x.jsonl", uzycie("ExitPlanMode", {}, "toolu_9"))
    wpis_plan(ss, tp)
    zamiataj(ss)
    assert len(names(ss)) == 1


def test_klucz_cytowany_jako_wolny_tekst_nie_gasi(ss, tdir):
    """Substring matching would clear EVERY live block: the key is always in the tail,
    because it sits in its own `tool_use` record."""
    tp = zapisz(tdir / "x.jsonl", uzycie("ExitPlanMode", {}, "toolu_9"),
                {"type": "assistant", "timestamp": "2026-08-07T10:01:00.000Z",
                 "message": {"content": [{"type": "text",
                                          "text": "w logu widze toolu_9, sprawdz to"}]}})
    wpis_plan(ss, tp)
    zamiataj(ss)
    assert len(names(ss)) == 1


def test_brak_pliku_transkryptu_nie_gasi(ss, tdir):
    wpis_plan(ss, str(tdir / "nie-ma-mnie.jsonl"))
    zamiataj(ss)
    assert len(names(ss)) == 1


def test_zepsuta_linia_w_srodku_ogona_nie_gubi_wyniku(ss, tdir):
    """`_safe` on each line separately: one truncated line must not eat the rest of the tail."""
    tp = zapisz(tdir / "x.jsonl", uzycie("ExitPlanMode", {}, "toolu_9"),
                '{"type": "user", "message": {"content": [{"type": "tool_re',
                wynik("toolu_9", pozniej(ss)))
    wpis_plan(ss, tp)
    zamiataj(ss)
    assert names(ss) == []


def test_nieczytelny_transkrypt_jednego_wpisu_nie_blokuje_drugiego(ss, tdir):
    """`_safe` around the whole read of one file — otherwise a single blocked transcript
    would hang every other block on the machine."""
    kaput = tdir / "katalog-nie-plik.jsonl"
    kaput.mkdir()                                    # open() on a directory raises
    ok = zapisz(tdir / "x.jsonl", uzycie("ExitPlanMode", {}, "toolu_b"),
                wynik("toolu_b", pozniej(ss)))
    wpis_plan(ss, str(kaput), tuid="toolu_a")
    wpis_plan(ss, ok, tuid="toolu_b")
    zamiataj(ss)
    assert names(ss) == ["%s__main__toolu_a.json" % SID]


def test_transkrypt_krotszy_niz_ogon_nie_traci_pierwszej_linii(ss, tdir):
    """The first line is incomplete ONLY when the read really started in mid-file."""
    tp = zapisz(tdir / "x.jsonl", wynik("toolu_9", pozniej(ss)))
    wpis_plan(ss, tp)
    zamiataj(ss)
    assert names(ss) == []


def test_wyslany_wpis_nie_niesie_pol_lokalnych(ss, tdir):
    """`transcript_path` carries the human's home directory name and has no recipient
    in `SessionAlert`; neither does `prompt_id`. On disk they must stay."""
    tp = str(tdir / "x.jsonl")
    wpis_permission(ss, tp, {"command": "ls"})
    wyslany = ss.wyslane[-1]["entries"][0]
    assert "transcript_path" not in wyslany and "prompt_id" not in wyslany
    na_dysku = ss.read_entry(names(ss)[0])
    assert na_dysku["transcript_path"] == tp and na_dysku["prompt_id"] == "p1"


# --- `permission` entries: the key is a hash, so the id must be recovered by recomputing
def test_permission_domyka_sie_przez_przeliczenie_call_key(ss, tdir):
    ti = {"command": "git push --force"}
    tp = zapisz(tdir / "x.jsonl", uzycie("Bash", ti, "toolu_x"),
                wynik("toolu_x", pozniej(ss)))
    wpis_permission(ss, tp, ti)
    nazwa = names(ss)[0]
    assert "toolu_x" not in nazwa, \
        "klucz wpisu musi byc hashem, inaczej przypadek przechodzi po regule question/plan"
    zamiataj(ss)
    assert names(ss) == []


def test_to_samo_narzedzie_z_innym_input_nie_gasi(ss, tdir):
    ti = {"command": "git push --force"}
    tp = zapisz(tdir / "x.jsonl", uzycie("Bash", {"command": "git status"}, "toolu_x"),
                wynik("toolu_x", pozniej(ss)))
    wpis_permission(ss, tp, ti)
    zamiataj(ss)
    assert len(names(ss)) == 1


@pytest.mark.parametrize("is_error", [True, False])
def test_identyczny_retry_bez_wyniku_nie_gasi(ss, tdir, is_error):
    """Measured: 0.24% of calls have a byte-identical twin inside a 32 KB window, and the
    "refused, Claude repeats the identical call" scenario occurred 22 times in the corpus.
    Both have the same `call_key`, so ONLY the last `tool_use` record counts."""
    ti = {"command": "git push --force"}
    tp = zapisz(tdir / "x.jsonl", uzycie("Bash", ti, "toolu_1"),
                wynik("toolu_1", pozniej(ss), is_error=is_error),
                uzycie("Bash", ti, "toolu_2"))
    wpis_permission(ss, tp, ti)
    zamiataj(ss)
    assert len(names(ss)) == 1, "zgaszona blokada, ktorej czlowiek jeszcze nie widzial"


def test_kontrola_pozytywna_gdy_mlodszy_retry_tez_rozstrzygniety(ss, tdir):
    """The same state with the safeguard not met — without this the test above passes also
    with the mechanism cut out."""
    ti = {"command": "git push --force"}
    tp = zapisz(tdir / "x.jsonl", uzycie("Bash", ti, "toolu_1"),
                wynik("toolu_1", pozniej(ss)), uzycie("Bash", ti, "toolu_2"),
                wynik("toolu_2", pozniej(ss)))
    wpis_permission(ss, tp, ti)
    zamiataj(ss)
    assert names(ss) == []


def test_wynik_z_innej_tury_nie_gasi(ss, tdir):
    """`prompt_id` spans the human's whole turn (measured 5-12 calls, 89-199 s)."""
    ti = {"command": "ls"}
    tp = zapisz(tdir / "x.jsonl", uzycie("Bash", ti, "toolu_x"),
                wynik("toolu_x", pozniej(ss), prompt_id="p2"))
    wpis_permission(ss, tp, ti)
    zamiataj(ss)
    assert len(names(ss)) == 1


def test_wynik_starszy_niz_wejscie_w_blokade_nie_gasi(ss, tdir):
    """Closes a retry in the SAME turn that `promptId` does not catch. Comparison on
    parsed time: lexicographically '...:40.816Z' < '...:40Z', because '.' < 'Z'."""
    ti = {"command": "ls"}
    tp = zapisz(tdir / "x.jsonl", uzycie("Bash", ti, "toolu_x"),
                wynik("toolu_x", ss._iso(time.time() - 300)))
    wpis_permission(ss, tp, ti)
    zamiataj(ss)
    assert len(names(ss)) == 1


def test_wpis_bez_prompt_id_nie_jest_domykany(ss, tdir):
    """An older probe. A hash over an empty string cannot tell turns apart, so leave it be."""
    ti = {"command": "ls"}
    tp = zapisz(tdir / "x.jsonl", uzycie("Bash", ti, "toolu_x"),
                wynik("toolu_x", pozniej(ss), prompt_id=None))
    wpis_permission(ss, tp, ti, prompt_id=None)
    zamiataj(ss)
    assert len(names(ss)) == 1


# --- subagent: the hook carries the PARENT's `transcript_path`, records live in a separate file
def test_wpis_subagenta_domyka_sie_z_jego_wlasnego_pliku(ss, tdir):
    rodzic = zapisz(tdir / "x.jsonl", uzycie("ExitPlanMode", {}, "toolu_9"))
    zapisz(tdir / SID / "subagents" / "agent-a1d9fb.jsonl",
           uzycie("ExitPlanMode", {}, "toolu_9"), wynik("toolu_9", pozniej(ss)))
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="ExitPlanMode", tool_input={},
                                tool_use_id="toolu_9", transcript_path=rodzic,
                                agent_id="a1d9fb", agent_type="general-purpose"))
    assert len(names(ss)) == 1
    zamiataj(ss)
    assert names(ss) == [], "plik subagenta nie zostal wyliczony ze `agent_id`"


def test_brak_pliku_subagenta_nie_siega_do_rodzica(ss, tdir):
    """The parent has a resolution, but not of THIS call — the subagent has its own file."""
    rodzic = zapisz(tdir / "x.jsonl", uzycie("ExitPlanMode", {}, "toolu_9"),
                    wynik("toolu_9", pozniej(ss)))
    ss.alert_dispatch(CFG, hook("PreToolUse", tool_name="ExitPlanMode", tool_input={},
                                tool_use_id="toolu_9", transcript_path=rodzic,
                                agent_id="a1d9fb"))
    zamiataj(ss)
    assert len(names(ss)) == 1


# ----------------------------------------------------------------- writing and sending
def test_o_excl_zachowuje_since(ss):
    """Re-entering the same block must not move the stamp — otherwise
    'waiting 40 min' would reset on every twitch."""
    h = hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"})
    ss.alert_dispatch(CFG, h)
    since = ss.snapshot()[0]["since"]
    time.sleep(1.05)
    ss.alert_dispatch(CFG, h)
    assert ss.snapshot()[0]["since"] == since


def test_post_tylko_przy_zmianie_zbioru(ss):
    h = hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"})
    ss.alert_dispatch(CFG, h)
    assert len(ss.wyslane) == 1
    ss.alert_dispatch(CFG, h)                     # the same block, set unchanged
    assert len(ss.wyslane) == 1
    ss.alert_dispatch(CFG, hook("Stop"))
    assert len(ss.wyslane) == 2
    assert ss.wyslane[-1]["entries"] == [], "opustoszenie zbioru MUSI dojsc do panelu"


def test_reczne_usuniecie_plikow_dochodzi_do_panelu(ss):
    """The emergency hatch from the RUNBOOK: `del %LOCALAPPDATA%\\...\\session-status\\*`. The
    deletion comes from OUTSIDE the probe, so nothing published it and the panel kept a card
    for a block that is gone. The sweep now compares the set against the marker
    unconditionally."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    assert len(ss.wyslane) == 1 and ss.wyslane[-1]["entries"]
    for n in names(ss):                              # this is what `del` does
        os.remove(os.path.join(ss.STATEDIR, n))
    ss.alert_dispatch(CFG, hook("Stop", session_id="inna-sesja"))
    assert ss.wyslane[-1]["entries"] == [], "panel zostalby z nieaktualna karta"


def test_wygasniecie_ttl_dochodzi_do_panelu(ss):
    """`sweep_ttl` deletes and never publishes — the same root cause as the `del` hatch.
    Symptom: two days later the block is long gone and the triangle is still hanging."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    nazwa = names(ss)[0]
    stary = time.time() - 2 * ss.DEFAULT_TTL_S
    os.utime(os.path.join(ss.STATEDIR, nazwa), (stary, stary))
    ss.alert_dispatch(CFG, hook("Stop", session_id="inna-sesja"))
    assert names(ss) == []
    assert ss.wyslane[-1]["entries"] == []


def test_wygasniecie_jednego_z_dwoch_wpisow_dochodzi_do_panelu(ss):
    """The same failure with a NON-EMPTY directory: TTL takes one entry of two, in that
    run the probe deletes nothing, and the server keeps both. That is why reconciliation
    must not sit behind `if hit`."""
    for cmd in ("ls", "pwd"):
        ss.alert_dispatch(CFG, hook("PermissionRequest", session_id="sesja-%s" % cmd,
                                    tool_name="Bash", tool_input={"command": cmd}))
    rejestr(ss, "sesja-ls", "sesja-pwd", SID)       # both sessions ARE ALIVE, so none is swept
    assert len(ss.wyslane[-1]["entries"]) == 2
    stary = time.time() - 2 * ss.DEFAULT_TTL_S
    p = os.path.join(ss.STATEDIR, names(ss)[0])
    os.utime(p, (stary, stary))
    ss.alert_dispatch(CFG, hook("Stop"))
    assert len(names(ss)) == 1
    assert len(ss.wyslane[-1]["entries"]) == 1, "serwer trzymalby wpis, ktorego nie ma"


def test_zbior_bez_zmian_nie_generuje_post_ow(ss):
    """An unconditional `publish()` must not mean "send on every event" — the marker
    decides that. Otherwise an idle machine would talk to the server every turn."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    ile = len(ss.wyslane)
    for _ in range(5):
        ss.alert_dispatch(CFG, hook("Stop", session_id="inna-sesja"))
    assert len(ss.wyslane) == ile


def test_pusty_katalog_i_pusty_znacznik_milcza(ss):
    """The commonest run on a machine with no block: nothing to reconcile and nothing flies."""
    for _ in range(5):
        ss.alert_dispatch(CFG, hook("Stop"))
        ss.alert_dispatch(CFG, hook("UserPromptSubmit"))
    assert ss.wyslane == []


def test_sciezka_goraca_nie_uzgadnia(ss):
    """`PostToolUse` fires on EVERY tool call. Reconciling there would mean reading the
    directory and the marker on the densest event this probe ever sees."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    for n in names(ss):
        os.remove(os.path.join(ss.STATEDIR, n))
    ile = len(ss.wyslane)
    ss.alert_dispatch(CFG, hook("PostToolUse", tool_name="Read", tool_input={},
                                tool_use_id="toolu_x"))
    assert len(ss.wyslane) == ile, "galaz zamykajaca zaczela chodzic do serwera"


def test_nieudany_post_nie_zapisuje_znacznika(ss, monkeypatch):
    monkeypatch.setattr(ss, "post", lambda *a, **kw: None)
    h = hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"})
    ss.alert_dispatch(CFG, h)
    assert not os.path.exists(ss.POSTED), "inaczej zguba nie powtorzylaby sie nigdy"


def test_tryb_lokalny_bez_konfiguracji(ss):
    """Without `alert_url` the script writes files and raises a toast, sends nothing. That
    is a legal state, not a failure — and the fallback channel when the server is down."""
    ss.alert_dispatch({}, hook("PermissionRequest", tool_name="Bash",
                         tool_input={"command": "ls"}))
    assert len(names(ss)) == 1
    assert ss.wyslane == []


def test_polskie_znaki_w_detalu(ss, tmp_path):
    """Bez jawnego utf-8 cp1250 wywala sie na polskiej sciezce — i to jest wyjatek
    w skrypcie, ktory ma prawa nie rzucac."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Edit",
                          tool_input={"file_path": r"C:\Zażółć\gęślą\jaźń.py"}))
    assert "jaźń" in ss.snapshot()[0]["detail"]


def test_detail_jest_przyciety(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "x" * 500}))
    assert len(ss.snapshot()[0]["detail"]) == ss.DETAIL_MAX


# ----------------------------------------------------------------------- project name
def test_worktree_daje_nazwe_projektu_a_nie_agenta(ss):
    """`basename(cwd)` would give 'agent-a00ce9ba287d12ab1', and a walk-up to `.git` would
    stop at the worktree, because there `.git` is a FILE, not a directory."""
    transcript = str(Path.home() / ".claude" / "projects"
                     / "z--projects-claude-usage-monitor" / "x.jsonl")
    assert ss.project_name(r"Z:\projects\claude-usage-monitor\.claude\worktrees"
                           r"\agent-a00ce9ba287d12ab1",
                           transcript) == "claude-usage-monitor"


def test_podkatalog_nie_zostaje_nazwa_projektu(ss):
    """Measured: 38 of 73 sessions report more than one `cwd`. The header would show
    'src' instead of the project name."""
    transcript = str(Path.home() / ".claude" / "projects"
                     / "z--projects-claude-usage-monitor" / "x.jsonl")
    assert ss.project_name(r"Z:\projects\claude-usage-monitor\frontend\src",
                           transcript) == "claude-usage-monitor"


def test_rozjazd_wielkosci_litery_dysku_nie_psuje_dopasowania(ss):
    """~/.claude.json REALLY does hold duplicate keys differing only in the case of the
    drive letter — the same drift affects `cwd`."""
    transcript = str(Path.home() / ".claude" / "projects"
                     / "z--projects-claude-usage-monitor" / "x.jsonl")
    assert ss.project_name(r"z:\Projects\Claude-Usage-Monitor",
                           transcript) == "Claude-Usage-Monitor"


def test_brak_transkryptu_spada_na_walk_up(ss, tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    (tmp_path / "repo" / "sub").mkdir()
    assert ss.project_name(str(tmp_path / "repo" / "sub"), None) == "repo"


def test_project_name_nigdy_nie_rzuca(ss):
    for cwd in (None, "", 42, r"C:\\"):
        ss.project_name(cwd, None)


# --------------------------------------------------------------------- robustness
def test_smieci_na_wejsciu_nie_rzucaja(ss):
    for h in ({}, {"hook_event_name": "CosNowego"},
              {"hook_event_name": "PermissionRequest"},
              {"hook_event_name": "PostToolUse", "session_id": None},
              {"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion"}):
        ss.alert_dispatch(CFG, h)


def test_uszkodzony_plik_stanu_nie_psuje_snapshotu(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                          tool_input={"command": "ls"}))
    with open(os.path.join(ss.STATEDIR, "smieci.json"), "w", encoding="utf-8") as f:
        f.write("{to nie jest json")
    assert len(ss.snapshot()) == 1


def test_call_key_jest_stabilny_i_zalezy_od_promptu(ss):
    ti = {"command": "git status"}
    assert ss.call_key("Bash", ti, "p1") == ss.call_key("Bash", ti, "p1")
    assert ss.call_key("Bash", ti, "p1") != ss.call_key("Bash", ti, "p2")
    assert ss.call_key("Bash", ti, "p1") != ss.call_key("Edit", ti, "p1")


def test_zapora_przed_rekurencja(ss, monkeypatch):
    """The probe's `claude -p "/usage"` is an ordinary Claude Code session and fires the
    same hooks. The throttle alone will not stop it — every child has its own clock."""
    monkeypatch.setenv(ss.CHILD_ENV, "1")
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(
        hook("PermissionRequest", tool_name="Bash", tool_input={"command": "ls"}))))
    assert ss.main() == 0
    assert names(ss) == []


# ---------------------------------------------------------------------- flag
def test_flaga_wylacza_wszystko(ss):
    ss.alert_dispatch(dict(CFG, session_status=False),
                      hook("PermissionRequest", tool_name="Bash",
                           tool_input={"command": "ls"}))
    assert names(ss) == []
    assert ss.wyslane == []


def test_brak_klucza_znaczy_wlaczone(ss):
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    assert len(names(ss)) == 1


def test_wylaczenie_gasi_to_co_wisi(ss):
    """A bare `return` would not be enough: a block in progress at the moment of switching
    off would stay on the panel until the server TTL, because nobody would send a correction."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    assert len(names(ss)) == 1 and len(ss.wyslane) == 1
    ss.alert_dispatch(dict(CFG, session_status=False), hook("PostToolUse"))
    assert names(ss) == []
    assert ss.wyslane[-1]["entries"] == [], "panel musi dostac pusty zbior"


def test_wylaczone_przy_pustym_katalogu_nie_gada_do_serwera(ss):
    """Otherwise a switched-off feature would send a POST on EVERY hook event."""
    for _ in range(5):
        ss.alert_dispatch(dict(CFG, session_status=False), hook("PostToolUse"))
    assert ss.wyslane == []


def test_wylaczone_nie_uzgadnia_nawet_przy_rozjezdzie(ss):
    """Reconciling the set against the marker MUST NOT bypass the switch. The marker ends up
    out of step with the disk, but a switched-off feature has to keep quiet — cleanup happens
    by switching it back on, or by hand."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    for n in names(ss):
        os.remove(os.path.join(ss.STATEDIR, n))     # drift: disk empty, marker not
    ile = len(ss.wyslane)
    for event in ("Stop", "UserPromptSubmit", "SessionEnd", "SessionStart"):
        ss.alert_dispatch(dict(CFG, session_status=False), hook(event))
    assert len(ss.wyslane) == ile


def test_tryb_lokalny_nie_wychodzi_do_sieci_przy_uzgadnianiu(ss):
    """Without `alert_url` the signaller works locally only. Reconciliation computes the
    snapshot and reads the marker, but must not send anything."""
    lokalny = {"toast": False}
    ss.alert_dispatch(lokalny, hook("PermissionRequest", tool_name="Bash",
                                    tool_input={"command": "ls"}))
    assert len(names(ss)) == 1 and ss.wyslane == []
    for n in names(ss):
        os.remove(os.path.join(ss.STATEDIR, n))
    for event in ("Stop", "UserPromptSubmit", "SessionStart"):
        ss.alert_dispatch(lokalny, hook(event))
    assert ss.wyslane == []
    assert not os.path.exists(ss.POSTED), "tryb lokalny nie zapisuje znacznika"


def test_wlasny_ttl_z_konfiguracji_jest_respektowany(ss):
    """`blocked_ttl_sec` from `config.json` still works, and its effect now reaches the panel."""
    ss.alert_dispatch(CFG, hook("PermissionRequest", tool_name="Bash",
                                tool_input={"command": "ls"}))
    nazwa = names(ss)[0]
    stary = time.time() - 120
    os.utime(os.path.join(ss.STATEDIR, nazwa), (stary, stary))
    ss.alert_dispatch(dict(CFG, blocked_ttl_sec=3600), hook("Stop", session_id="inna"))
    assert names(ss) == [nazwa], "wpis zginal przed swoim wlasnym TTL"
    ss.alert_dispatch(dict(CFG, blocked_ttl_sec=60), hook("Stop", session_id="inna"))
    assert names(ss) == []
    assert ss.wyslane[-1]["entries"] == []


# --------------------------------------------------------------- merged into the probe
def _uruchom(ss, monkeypatch, payload, throttle_swiezy=True):
    """The probe's full `main()` with a substituted stdin."""
    with open(ss.CONFIG, "w", encoding="utf-8") as f:
        json.dump({"alert_url": CFG["alert_url"], "ingest_token": "t",
                   "throttle_sec": 3600}, f)
    if throttle_swiezy:
        with open(ss.THROTTLE_FILE, "w") as f:
            f.write(str(time.time()))
    # `ensure_ascii=False` is LOAD-BEARING here, not cosmetic: the default escaping would
    # give a pure-ASCII payload, so no decoder would have anything to corrupt and the
    # encoding test would always pass. Claude Code sends raw characters.
    monkeypatch.setattr("sys.stdin", _Stdin(json.dumps(payload, ensure_ascii=False)))
    return ss.main()


def test_alert_odpala_sie_PRZED_throttlem(ss, monkeypatch):
    """A regression on the ordering inside `main()`. The probe throttle is 60 s; were the
    alert to stand behind it, a block would be visible only after a minute or — on dense
    events — not at all."""
    assert _uruchom(ss, monkeypatch,
                    hook("PermissionRequest", tool_name="Bash",
                         tool_input={"command": "git push --force"})) == 0
    assert len(names(ss)) == 1, "throttle sondy zjadl alert"


def test_sonda_milczy_na_stdout_przy_permission_request(ss, monkeypatch, capsys):
    """`PermissionRequest` is a DECISION hook: anything on stdout changes the prompt's
    behavior. The contract reads 'exit 0 with no JSON = leave the decision to the human'."""
    _uruchom(ss, monkeypatch, hook("PermissionRequest", tool_name="Bash",
                                   tool_input={"command": "ls"}))
    zebrane = capsys.readouterr()
    assert zebrane.out == ""


def test_zapora_przed_rekurencja_ucina_takze_alert(ss, monkeypatch):
    monkeypatch.setenv(ss.CHILD_ENV, "1")
    assert _uruchom(ss, monkeypatch,
                    hook("PermissionRequest", tool_name="Bash",
                         tool_input={"command": "ls"})) == 0
    assert names(ss) == []


def test_stdin_hooka_czytany_jako_utf8(ss, monkeypatch):
    """Payload hooka to UTF-8, ale `sys.stdin` w trybie tekstowym dekodowal go
    kodowaniem locale — na ekranie i w toascie wychodzilo 'umieraÄ‡' zamiast
    'umierac' z ogonkami. Asercja idzie przez `snapshot()`, bo on ma jawne utf-8;
    gole `open()` przeczytaloby poprawny plik jako cp1250 i dalo czerwien na dobrym
    kodzie."""
    assert _uruchom(ss, monkeypatch,
                    hook("PermissionRequest", tool_name="Bash",
                         tool_input={"command": "echo zażółć gęślą jaźń"})) == 0
    assert "zażółć gęślą jaźń" in ss.snapshot()[0]["detail"]


def test_stdin_z_bajtem_spoza_cp1250_nie_gubi_alertu(ss, monkeypatch):
    """Twardszy tryb tej samej awarii, i to on boli bardziej. cp1250 nie ma
    odpowiednika dla 0x81/0x83/0x88/0x90/0x98, a `sys.stdin` ma
    `errors=surrogateescape` — wiec 'Ł' (C5 81) nie psul sie na ekranie, tylko
    stawal sie samotnym surogatem. Ten wywracal `write_excl` na `.encode("utf-8")`
    ("surrogates not allowed"), a tam stoi `except Exception: pass`: plik wpisu
    powstawal PUSTY. Skutek: toast leci, `read_entry` nie parsuje, `snapshot()`
    pomija, alert nigdy nie dociera na panel — a klucz jest juz zajety, wiec
    ponowienie tez nic nie da."""
    assert _uruchom(ss, monkeypatch,
                    hook("PermissionRequest", tool_name="Edit",
                         tool_input={"file_path": r"C:\Łukasz\gęś.py"})) == 0
    assert len(ss.snapshot()) == 1
    assert "Łukasz" in ss.snapshot()[0]["detail"]


class _Stdin:
    """The double must decode BADLY, or there is nothing to catch.

    The real `sys.stdin` in a hook process gets UTF-8 bytes and decodes them with
    the locale encoding (cp1250 on the machine where this was measured) — `.read()`
    reproduces exactly that, while `.buffer` carries the truth. A double returning
    a ready-made `str` was a convenient fiction: it passed the same before the fix
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
