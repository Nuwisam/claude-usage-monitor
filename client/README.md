# Client — limit probe

Script run from Claude Code hooks. **Sends no request to `api.anthropic.com`.**
It delegates the measurement to Claude Code itself (`claude -p "/usage"`), reads the result
from disk, and sends the monitor only that result.

## Why we don't query the endpoint ourselves

Version 2 of the probe called `GET /api/oauth/usage` with its own OAuth token from
`.credentials.json`, impersonating `User-Agent: claude-code/…` (without that you land in the
aggressive 429 bucket). Anthropic's terms make no exception: OAuth tokens from
Free/Pro/Max accounts must not be used *"in any other product, tool, or service"* — there is
no loophole there for read-only use.

Version 3 removes the whole problem: the request is made by the first-party client, with its
own token, which it refreshes itself. We only read what it left behind.

## Two sources, because freshness and completeness live in different places

Measured, not guessed:

| Source | Freshness | What it contains |
|---|---|---|
| stdout of `claude -p "/usage"` | **fresh on every call** | percentages of the main windows, as text |
| `~/.claude.json` -> `cachedUsageUtilization` | <= 5 min | **the full raw response body** — `spend`, `extra_usage`, `limits[]`, all 17 buckets |

The split comes from the fact that the five-minute throttle in Claude Code applies to the
**write to disk**, not to the fetch — that's why interactive `/usage` always shows current
data, while the file can be a few minutes older.

The probe merges one with the other: structure from the cache, fresh percentages laid on top.
The result has **exactly the same shape** as the old HTTP response, so `backend/app/parsing.py`
did not need a single change. Losing the decimal places from `Math.floor` in stdout **is not a
loss** — the API itself returns whole numbers (verified against the raw payload).

## `/usage` does not consume the limit

`/usage` is registered twice in the binary; the `supportsNonInteractive: true` variant is the
one active in `-p` mode and returns `{type:"text"}`, which sets `shouldQuery=false`.
Measured over 6 calls: `num_turns=0`, `duration_api_ms=0`, `total_cost_usd=0`.

**But this only works when the argument hits the local command.** If it misses — a normal,
paid model turn runs instead. The probe detects this from `num_turns>0` and discards such a dump.

> **Trap:** Git Bash on Windows converts arguments starting with `/`, so
> `claude -p "/usage"` becomes `claude -p "C:/Program Files/Git/usage"` and you get a paid
> turn. Test from PowerShell or set `MSYS_NO_PATHCONV=1`.

## Files

| File | Role |
|---|---|
| `usage-probe.py` | **Source of truth.** The probe **and** the blocked-session signaller — this is where you edit them |
| `analyze-samples.py` | Analysis of the local log — rate of change, errors, accounts |

**Why one file, not two.** The signaller used to be a separate script wired into ten events, of
which **seven** were already covered by the probe — and since these are `PreToolUse` and
`PostToolUse`, i.e. the ones firing on every tool call, in the **overwhelming majority of runs**
two CPythons were starting instead of one. Re-measured on the current tree (100 runs per
variant, interleaved by rotation, median of paired differences, every variant on the local disk,
the probe entered with `CUM_PROBE_CHILD=1` so that `main()` returns on its first line): folding
the signaller code into the probe process costs **2.3 ms** (95% CI 1.7-2.6, slower in 92 of 100
pairs), a separate process **37.4 ms** (37.0-37.9). Over a day, at ~21,000 events: **+48 s**
instead of **+785 s**.

The merge-time numbers were 2.7 ms for the projection and 1.7 ms for the post-merge control, and
the re-measurement lands between them — they were never in conflict, both are the same quantity
inside its own spread. What has changed since is the file: 1317 lines at the merge (+542 rather
than the forecast +648, because merging removed duplicates), 1755 today, of which the English
translation is +437.

The previous justification for the split — *"+0.294 ms for every line added to the probe"* —
was **overstated ~71-fold**; the real figure is 0.0041 ms/line, and it held across the
translation as well (437 lines for 1.8 ms — 0.0040 ms/line). The split also forced a
duplicate: of 13 names shared by both files, 9 were byte-for-byte identical, and
`_extract_block` (~40 lines) differed only in a comment.

Consequence: **alerts require the probe.** There is no longer a separate script for a machine
that wants notifications without limit measurement.

This is the **source**: `backend/tests/test_probe_parsing.py` loads it by a fixed path, and
this is where changes land. Every copy issued to machines is a **release** and **may be
older** than HEAD — that is correct, because publication is meant to be a decision, not a side
effect of a push; an automatic sync would push a work-in-progress version to remote machines.

That's why **every behavior change requires bumping `SCRIPT_VERSION`**: the version rides along
in every batch, so the difference is visible in `/api/machines` under `scriptVersion`. Without
the bump, two different probes are indistinguishable.

## Installation

The instructions are complete: from a bare machine to a confirmed measurement in the monitor.
The order of the steps matters — we check the secrets **before** touching `settings.json`, so
that a failed installation leaves the file untouched.

### 0. Requirements

| What's needed | How to check | If missing |
|---|---|---|
| `claude` on `PATH` | `claude --version` | The full path goes into `config.json` as `claude_bin`. Without it the probe has nothing to delegate the measurement to, and logs `no-claude-in-path` |
| A working Python | in order: `python3 --version`, `python --version`, `py -3 --version` | Stop — the hook has nothing to run with |
| `~/.claude/settings.json` | the file exists and parses as JSON | No file: create `{}`. **It exists but doesn't parse: stop.** Do not overwrite it — it holds Claude Code's entire configuration |

**Remember the interpreter name that worked — that exact one goes into the hook.**
Do not put an absolute path there: a Python upgrade moves it, and all hooks silently die.
On Linux and macOS, usually only `python3` exists.

### 1. Machine token — this step happens on the server

Ingest authorizes **each machine separately**, with a token from `INGEST_TOKENS`. The remote
machine has no access to the monitor host, so it cannot generate a token for itself. A
candidate token is produced like this:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

and on the monitor host it's appended to `INGEST_TOKENS` in `.env` — as `,<token>:<machine-name>`,
where `<machine-name>` is the label shown in the panel (`desktop`, `laptop`, `vps-1`). Then,
in the directory with `docker-compose.yml`:

```bash
docker compose up -d
```

**`docker compose up -d`, never `restart`.** `INGEST_TOKENS` is read at container *creation*
time; `restart` leaves the old set of variables in place and the fresh token stays invalid —
a symptom indistinguishable from a mistyped token.

The second secret, `INGEST_EDGE_KEY`, is checked by Apache before the application, so scanners
don't reach the Python process. It is **shared across all machines** — take the existing one
from the host's `.env`, don't invent a new one. The format of both is in
[`.env.example`](../.env.example), server deployment in
[`README.md`](../README.md#server-deployment).

### 2. Configuration

`%LOCALAPPDATA%\claude-usage-monitor\config.json` (Windows)
or `~/.local/state/claude-usage-monitor/config.json` (Linux, macOS):

```json
{
  "ingest_url": "https://usage.example.org/claude-usage/api/ingest",
  "ingest_token": "<token of THIS machine, from step 1>",
  "edge_key": "<INGEST_EDGE_KEY>",
  "throttle_sec": 60,
  "claude_bin": "<optional, when `claude` is not on PATH>"
}
```

The file is **deliberately outside the repo** — a machine's token has no business reaching git.
The probe derives the data directory from `%LOCALAPPDATA%` / `~/.local/state` at run time,
**not** from its own location — so it doesn't matter where the script itself lives.

Without `config.json` the probe runs in **local-only mode**: it measures and logs, sends
nothing. That is a valid state, not a failure. The blocked-session signaller (step 7) is then
still **on** and pops toasts on Windows — only the send goes silent. `"session_status": false`
turns it off.

### 3. Handshake — we check the secrets before touching `settings.json`

An empty POST registers the machine and returns before any account check, so it tests exactly
those two secrets and nothing else. `INGEST_URL`, `TOKEN` and `EDGE_KEY` are the three values
just entered into `config.json`:

```bash
curl -s -o /tmp/hs.txt -w '%{http_code}' -X POST "$INGEST_URL" \
  -H "Authorization: Bearer $TOKEN" -H "X-Ingest-Key: $EDGE_KEY" \
  -H 'Content-Type: application/json' -d '{}'
```

| Status | Body | Meaning |
|---|---|---|
| `200` | `{"ok":false,…,"batchId":N}` | Both secrets are good. `ok:false` is **correct** — an empty batch carries no samples |
| `403` | **HTML from Apache** | Wrong or missing edge key. Don't parse the body as JSON before checking the status |
| `401` | `{"detail":{"reason":"invalid-token"}}` | The token isn't in `INGEST_TOKENS` — or the container was `restart`ed instead of recreated (step 1) |

**Until you get a 200, don't install the hooks.**

### 4. The hooks path holds a redirect, not a copy

The path
`%LOCALAPPDATA%\claude-usage-monitor\usage-probe.py` (Windows) or
`~/.local/state/claude-usage-monitor/usage-probe.py` (Linux, macOS) is the **contract** —
every entry in `settings.json` points at it. What sits there is a dozen or so lines of Python
that run the real probe from `SRC`. This is the **entire content of the file**, not an excerpt:

```python
#!/usr/bin/env python3
"""Redirect, not the probe. The real code lives under SRC."""
import os, runpy, sys

SRC = r"C:\path\to\repo\client\usage-probe.py"   # full path to the source file

if not os.path.isfile(SRC):        # rule 5: no source means silence, not a traceback
    sys.exit(0)
try:
    runpy.run_path(SRC, run_name="__main__")
except Exception:                  # NOT OSError — see below
    sys.exit(0)
```

The catch is **deliberately wide**. `except OSError` catches a dropped network share and a
sleeping disk, but not a truncated write: an incomplete source under `SRC` gives a `SyntaxError`
from `compile()` inside `runpy`, and that is not an `OSError` — the traceback would then go to
the hook on every one of the twelve events, until someone notices. `SystemExit` is a
`BaseException`, so `sys.exit(main())` from the probe still passes straight through here and
the exit code doesn't change. Nothing beyond that is hidden: the probe's body has its own
`except Exception`, so this catch is responsible only for **loading** failures.

This makes **editing in the repo take effect immediately**, with no copying after every change.
The original plan was a symbolic link there, but Windows refuses to create one without developer
mode or administrator rights (`Administrator privilege required`) — the redirect does the same
thing without any privileges, and identically on a remote machine. The only thing that differs
between them is **`SRC`**: here, the project repo; on a remote machine, the directory the copy
was issued to. The probe finds itself via `%LOCALAPPDATA%`, not via `__file__`, so the
redirect changes nothing for it.

Cost, broken down into components, because one number for the whole run always lies (median of
20-25 runs, `SRC` on a network drive):

| component | how much |
|---|---|
| bare CPython startup — the floor, can't go below it | 31-36 ms |
| the probe run directly, up to the recursion guard | 35.7 ms |
| the same thing through the redirect | 46.7 ms |
| **overhead of the redirect itself** (extra file read from the network drive + `runpy`) | **~11 ms** |
| a full run that ends at the throttle | 46.9 ms |
| a detached `claude -p "/usage"` | ~3.4 s |

The redirect isn't free, but it is still dwarfed by the cost the whole design is
about: the probe **does not wait** for `claude`, so that ~3.4 s never enters the run. A remote
machine reads `SRC` from a local disk and doesn't even pay that 11 ms.

Through version 6 there was an extra 23 ms here for the `socket`, `hashlib`, `http.client` and
`urllib.parse` imports on every run; from version 7 they are lazy, past the throttle.

**Measure these numbers on your own machine, don't copy them.** The machine they were produced
on had a spread of about 30 ms between min and max, and the component measurement diverges from
the whole-run measurement by up to a factor of three. A previous version of this paragraph
claimed the run finished "after ~30 ms" — that is, below the floor of starting the interpreter
alone.

### 5. Hooks in `~/.claude/settings.json`

Every entry is identical — the only thing that differs is the event it's attached to:

```json
{"type": "command", "async": true, "timeout": 10,
 "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}
```

On Linux and macOS, the same entry with `python3` and the path
`/home/<user>/.local/state/claude-usage-monitor/usage-probe.py`.

There are **nine** events for the measurement alone: `SessionStart`, `UserPromptSubmit`,
`PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PostToolBatch`, `SubagentStop`, `Stop`, and
`Notification` with `"matcher": "idle_prompt"`. The blocked-session signaller adds three more —
see step 7. In full:

```json
"hooks": {
  "PostToolUse":        [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "Stop":               [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "SessionStart":       [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "UserPromptSubmit":   [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "PreToolUse":         [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "PostToolUseFailure": [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "PostToolBatch":      [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "SubagentStop":       [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
  "Notification":       [{"matcher": "idle_prompt", "hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}]
}
```

**Merge, never overwrite.** `settings.json` holds the model, theme and hooks added by other tools —
the only acceptable edit is appending to the existing arrays.

`PostToolUse` with `"async": true` is the main trigger — measured at 0.24 ms overhead. `Stop`
closes the gap for turns with no tool calls, the rest shorten the gap after a session resumes.
At a 60 s throttle, firing more densely costs nothing.

**The path must be absolute, `%LOCALAPPDATA%` won't work.** On Windows, hooks are run through
Git Bash (visible in `claude --debug`: `Using bash path: C:\Program Files\Git\bin\bash.exe`),
and bash does not expand `%VARIABLE%` syntax.

**`UserPromptSubmit` requires `"async": true`.** Synchronously it blocks sending the prompt for
up to a 30-second timeout — this is the event where the hook *can* append context to the
prompt, so Claude Code waits for its result. With `async` there is nothing to wait for.

### 6. Verification — and what "working" means

**The first run doesn't measure anything.** The probe never waits for the child process:
`claude -p "/usage"` takes ~3.4 s, and the result is consumed by the **next** run. A correct
installation, after one cycle, therefore looks empty — don't diagnose before the second run.

1. Run the hook command manually, wait ~60 s (throttle), and run it a second time.
   On Windows do this **from PowerShell** or with `MSYS_NO_PATHCONV=1` — see the Git Bash trap
   above; from Git Bash you'll get a paid model turn instead of the free command.
2. `usage-samples.jsonl` in the data directory grows, and the last line has a `measurement`
   block with `source: cli_merged` or `cli_usage_cache`.
3. `spool.jsonl` stays empty. **A growing spool alongside a healthy log is the only signal that
   the send itself is failing** — the probe deliberately does not log the POST's result.
4. The machine is visible in the panel; `scriptVersion` in `/api/machines` says which code it's
   running.

Disabling or uninstalling: [`docs/RUNBOOK.md`](../docs/RUNBOOK.md#turning-off-collection),
section "Turning off collection".

### 7. Blocked-session signaller

The probe also detects the moment Claude Code has stopped and is **waiting for you**: a request
for permission to use a tool, `AskUserQuestion`, `ExitPlanMode`. It then pops a toast on this
machine and sends an alert to the monitor, so the panel on the desktop shows a card and a marker
next to the account.

There is no separate script and no second redirect — it is the same probe, the same process.

**What it reads while doing this** (besides `config.json` and its own state directory), always
read-only and only in the sweep branch, i.e. on `UserPromptSubmit`, `Stop`, `SessionEnd` and
`SessionStart` — and only when the state directory **is not empty**:

- **`~/.claude/sessions/`** (or `$CLAUDE_CONFIG_DIR/sessions/`) — the registry of live sessions
  that Claude Code maintains itself: a `<pid>.json` file with a `sessionId` field. A session-block
  entry no longer in the registry is an entry for a session that is no longer alive, and it is
  then cleared. The probe **never** checks pids: `os.kill(pid, 0)` on Windows maps to
  `TerminateProcess`, which would kill Claude Code sessions. Only the record's presence counts.
- **the session transcript** (`~/.claude/projects/<slug>/<session_id>.jsonl`, for a subagent
  `…/<session_id>/subagents/agent-<agent_id>.jsonl`) — the last 32 KB, to check whether the call
  already has a `tool_result`. The handle is held for the duration of one read and no longer.

When any of these sources can't be read in full, the probe **deletes nothing** — "don't know"
never means "empty" — and leaves one line in `usage-samples.jsonl` (`alert_skip`), so a broken
mechanism can be told apart from an absence of work.

**Configuration** — four keys appended to the existing `config.json`. The token and edge key are
**the same ones**, because the same machine is being authorized:

```json
{
  "session_status": true,
  "alert_url": "https://usage.example.org/claude-usage/api/session-alert",
  "toast": true,
  "blocked_ttl_sec": 86400
}
```

- **`session_status`** — the master switch, on by default. `false` turns off the state files,
  the toast and the send; limit measurement keeps working. Turning it off **also clears
  whatever is currently pending**: on the first event it deletes the entries and sends one
  empty set, so the marker on the panel disappears right away, not after a full day.
- Without **`alert_url`** the signaller works **locally only**: it writes state files and
  pops the toast, sends nothing. That is a valid state and, incidentally, an emergency
  channel — the toast still arrives even when the server is down.
- **`"toast": false`** disables just the notification; alerts to the panel keep going.

On the panel side there is a **separate** `session_alerts` flag in `panel.json`, and that's by
design: the probe sits on the machine with the session, the panel is on the desktop, and those
are often different machines. `session_status` turns off the **source**, `session_alerts` the
**display**.

**Hooks** — **three** more are added to the probe's nine events, in the same form:

```json
"PermissionRequest":  [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
"PermissionDenied":   [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}],
"SessionEnd":         [{"hooks": [{"type": "command", "async": true, "timeout": 10, "command": "python \"C:/Users/<user>/AppData/Local/claude-usage-monitor/usage-probe.py\""}]}]
```

Twelve events total. `PermissionRequest` is a **decision** hook — the contract reads "exit 0
with no JSON on stdout = hand the decision to the human", and the probe never prints anything
(enforced by `test_probe_is_silent_on_stdout_for_permission_request`).

**`Notification` is NOT used here.** Measured: it doesn't fire in the VS Code extension — zero
occurrences across 2,395 samples, independently confirmed by issue
[#29928](https://github.com/anthropics/claude-code/issues/29928). The payload also has neither
`tool_name` nor `tool_use_id`, so it couldn't be used to close out an entry anyway.
`PermissionRequest` covers all three states and fires **only** when a human is actually being
asked.

**Verification:**

1. Trigger something behind the permission gate (e.g. `Bash` outside the project directory) and
   **don't respond**. A file `<session_id>__main__<key>.json` must appear in
   `%LOCALAPPDATA%\claude-usage-monitor\session-status\`, and a toast on screen.
2. Answer "yes" — the file disappears.
3. Answer "no" or press Esc — the file **stays**, because a denial generates no hook event at
   all (measured 5/5). It clears at the next sweep event, and that's **any** session on this
   machine, not only the blocked one: a denial and Esc both write a `tool_result` into the
   transcript, and the probe reads the last 32 KB of every entry's transcript
   (`closed_by_transcript`). This is the only path for a session that **fell silent** after the
   denial — `Stop` doesn't fire on an interrupted turn, so without this the entry would hang
   until the 24 h TTL. An entry whose `tool_use` record doesn't fit in that tail (a `Write`
   with large content, ~1.3% of cases) will not be closed this way — it waits for the TTL.
4. Panel: a full-screen card, and after `alert_takeover_sec` (default 5 min) an accent marker
   next to the account name. The card belongs to a **set**: if a second session gets blocked in
   the meantime, the card stays — with both blocks — until that second one clears too.

Emergency escape hatch, in case something hangs:
`del %LOCALAPPDATA%\claude-usage-monitor\session-status\*`. The panel clears at the next sweep
event — the probe then compares the on-disk set against the marker of the last send and, on a
mismatch, sends a correction, even when it deleted nothing itself. The same path handles expiry
after `blocked_ttl_sec`, which used to delete the files without telling the server about it.

## Rules that must not be broken

**1. No request to `api.anthropic.com`.** Not a single one. This is the entire point of version 3.

**2. `accessToken` is not used to authenticate anything.** `.credentials.json` is read
exclusively for plan metadata (`subscriptionType`, `rateLimitTier`, `expiresAt`), read-only.
The dividing line sits at **using** the token, not at reading the file.

**3. We never call the token endpoint.** Refreshing belongs to Claude Code.

**4. Zero heavy imports on the hot path.** Standard library only. The top of the file has
only `sys, os, json, time, re` — the rest are local, in branches the hot path **never
touches**: `shutil` and `subprocess` in the CLI measurement, `ssl` in building the context,
`http.client` with `urllib.parse` in `post()`, `socket` with `hashlib` when assembling the
record, `hashlib` in `call_key`, `base64` with `subprocess` in `toast()`. The four from
`post()` and `record` moved down from the top in version 7 and were worth 23 ms on every run.

Note after the merge: the alert section's imports are **not** "past the throttle" — the
signaller runs before it. They are gated by something else: they only fire on ENTERING a
block and on LEAVING it, i.e. a couple of times per block, not on every tool call. The hot
path (`PostToolUse` with an empty state directory) ends at a single `scandir` and imports
nothing.

**Do not move them up, and do not add uses before the import line** —
a `NameError`/`UnboundLocalError` here would swallow rule 5, and the machine would stop
reporting without a single symptom.

**5. Never raises an exception.** `except: sys.exit(0)` at the top level.

**6. The probe never waits for the child process.** `claude -p "/usage"` takes ~3.4 s. The
result is consumed by the **next** run — that's why the hook costs ~47 ms, not 3.4 s.

**7. The recursion guard is mandatory.** The child process is a normal Claude Code session and
fires the `Stop` hook. The probe sets `CUM_PROBE_CHILD=1` for the child, and with that variable
set it exits immediately. **The throttle alone won't stop this** — every child has its own
clock.

## How it behaves

- **60 s throttle** — the marker is written **before** the call, so parallel hooks don't cause
  a stampede. The race window is non-zero: occasionally two calls go through instead of one.
  Harmless at this scale.
- **Bootstrap** — on a machine's first run, `cachedUsageUtilization` doesn't exist yet. The
  probe logs `no-cache`, runs `/usage`, and the measurement appears on the next cycle.
- **Expired-window guard** — we only get `resets_at` from the cache. When the window resets
  between the cache write and the read, the pair (percentage, `resets_at`) is contradictory.
  With a fresh percentage we zero `resets_at`; without a fresh one we drop the whole series
  from the cycle — publishing an old 95% as current would be a serious error, since the real
  figure is ~0%.
- **Absurd-value guard** — a percentage > 101 is rejected, not clamped to 100. Claude Code can
  leak the epoch from `resets_at` into the percentage field (#52326), and clamping would turn
  an obvious failure into a plausible-looking false alarm.
- **Spool** — a failed POST lands in `spool.jsonl` and rides along as `backlog[]` on the next
  successful one. The spool is trimmed **only after confirmation** of the number of accepted
  entries, so a mid-way failure loses nothing. Cap of 5000 lines.
- **Local log always**, regardless of whether the POST succeeded.

## Diagnostics

```bash
python client/analyze-samples.py          # rate of change, errors, accounts, switches
```

Working files in `%LOCALAPPDATA%\claude-usage-monitor\`:
`usage-samples.jsonl` (log), `spool.jsonl` (backlog), `last-probe.txt` (throttle),
`usage-cli.json` (stdout dump from `/usage`), `config.json`, `usage-probe.py` (**redirect**
to the source, not the probe — see Installation), `session-status\` (one file per ongoing
block) and `session-status-posted.txt` (a fingerprint of the last-sent set — this is how it
knows it doesn't need to resend).

The `measurement` field in every log entry says where the reading came from: `source`
(`cli_merged` / `cli_usage_cache`), `cache_age_s`, `fresh_age_s`, `fresh_at` (time of the
dump), `fresh_covered` (which series took their value from the dump), `sent_at` (age anchor —
see below), `fresh_skip` (why the dump wasn't used), `dropped` (what the guards rejected),
`spawn_error`.

### Dating: two sources, two times

`captured_at` is **always** the cache's time (`fetchedAtMs`), because everything comes from the
cache — including `spend` and `extra_usage`, which the dump never contains. The dump's time
rides separately, as `fresh_at`, and applies only to the series listed in `fresh_covered`.

Fusing both into one timestamp (which the v4 probe did) rejuvenated `spend` and `extra_usage`
by the entire age difference — by up to an hour. The backend decides which reading is current
based on that timestamp, so a machine with an older cache but a fresher dump would roll back
the state of exactly those two series. The monotonicity guard doesn't protect them: it requires
a known window boundary, and those two series **never** have a `resets_at`.

The actual measurement moment is computed by the **server**, not the client:

```
offset      = arrived_at − sent_at              # once per request
measured_at = min(ts + offset, arrived_at)      # per series
```

i.e. `received_at − age`. The machine's wall clock doesn't enter the calculation — only the
difference `sent_at − ts` matters, and that stays within a single clock. Entries coming from
the spool go through the same formula: their `sent_at` comes from the moment of the failed
attempt, so the age comes out right on its own.

An entry whose measurement is **newer** than its `sent_at` is rejected outright — the clock
went backward between the write and the send, so the dating is unreliable. The entry still
counts toward `backlogAccepted`, so the spool can still be trimmed.

### Dump older than the cache

Merging assumes the dump is fresher, but the dump is allowed to be up to 900 s old, and
ordinary work in Claude Code refreshes the cache within that window — the order can flip
(measured: 2 times out of 1646 measurements, by up to −105 s). When that happens the dump is
**ignored entirely** (`fresh_skip: dump-older-than-cache`).

The dangerous case is a window reset between the dump and the cache: the percentage would come
from the dump, i.e. from before the reset (95%), while `resets_at` comes **exclusively** from
the cache, i.e. already from the new window. The expired-window guard doesn't catch this — the
boundary is valid, so nothing looks contradictory — and we would be publishing 95% against a
window where the real figure is ~1%. The cost of rejecting is zero: what remains is the cache
value, which is both newer and more precise (stdout truncates to whole numbers).

**Certificates on Windows:** the CA store Python uses rejects the Let's Encrypt chain for some
hosts with a `certificate has expired` error, even though every link is valid — `curl` goes
through, Python doesn't. The client uses `certifi` when it's available. Should it be missing,
point to your own file via `"ca_bundle"` in `config.json`. **Do not disable verification.**
