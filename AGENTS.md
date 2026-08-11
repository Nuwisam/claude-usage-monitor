# AGENTS.md — working rules for this repo

Claude usage-limit monitor for multiple accounts. Details: `README.md`.
The groundwork this project rests on — the "Why this way, not another" section in
`README.md`.

## Rules that must not be broken

Each one exists because of a real problem, not out of caution.

**1. The probe sends no request to `api.anthropic.com`.**
The measurement is delegated to Claude Code itself (`claude -p "/usage"`), and
`client/usage-probe.py` only reads the result off disk. It has no HTTP client to Anthropic, no
`Authorization` header carrying an OAuth token, and no impersonation of
`User-Agent: claude-code/…`.

The reason is simple: Anthropic's terms say without exception that OAuth tokens from Free/Pro/Max
accounts must not be used *"in any other product, tool, or service"*. There is no carve-out for
read-only use. Version 2 of the probe called `/api/oauth/usage` itself and lived in a gray area;
version 3 removes the whole problem, because the request is made by the first-party client with
its own, self-refreshed token.

We still read `.credentials.json` — but **only for plan metadata** (`subscriptionType`,
`rateLimitTier`, `expiresAt`), read-only, and `accessToken` is never used for anything at all.
The dividing line sits at **using the token**, not at reading the file: reading it is a purely
local operation, sending a request with it is not.

We do not call the token endpoint (`grant_type=refresh_token`) and never will. Rotation of the
one-time refresh token is the main documented vector for account loss
([#38248](https://github.com/anthropics/claude-code/issues/38248), #47754, #53063).

**2. Zero heavy imports in the client.**
At the top of the file, only these are allowed: `sys`, `json`, `os`, `time`, `re`.
`socket`, `hashlib`, `http.client`, `urllib.parse`, `ssl`, `base64` and `subprocess` are
permitted, but **only lazily, off the hot path** — inside the body of `post()`, `main()`,
and in the entry/exit branches around the lock in the alert section. That section runs BEFORE
the throttle, so it is not "behind the throttle" — it is behind something else: it fires a few
times per block, not on every tool call. The hot path ends at a single
`scandir` and imports nothing. At the top of the file these modules used to cost
23 ms on **every** hook run, including the one that is about to exit on the throttle — which is
the overwhelming majority of runs. Measured: 59.5 → 35.7 ms median, on two identical
copies of the probe differing only in that one line.
`import httpx` alone is ~150 ms, and the script starts on **every** tool call.
The floor is hard: a bare CPython start is 31-36 ms (min-median of the reference measurement)
and nothing moves it — the whole budget rests on this rule.

Performance numbers here **must not be copied out of this file without re-measuring**. The
machine they were produced on had a spread of about 30 ms between min and max, and measuring
components in isolation (`python -c "import ..."`) can disagree with an end-to-end measurement
by as much as a factor of three. What's binding is a measurement of the whole probe run, not the
sum of module costs.

**3. The probe never raises an exception.**
`except Exception: sys.exit(0)` at the top level. This is the only code in the project that runs
on the critical path of your own work — a bug here breaks the session, not just a chart.

**4. `unknown` is never zero.**
`app/freshness.py` distinguishes "the window reset and nobody worked" (0% can be inferred) from
"the client is running but there are no samples" (a failure → `utilization: null`). A false,
confident-looking zero is this tool's worst failure mode — a user kicks off a big task and walks
straight into a wall. Regression test: `test_a_live_client_with_no_samples_gives_unknown`.

In pixels, this rule is carried by the **reading's age label**, not a separate drawing of the
track. The UI computes the value as `utilization ?? rawUtilization` — meaning that under
`unknown` it shows the **last MEASURED** percentage, never zero and never a word standing in for
a known number — and the label next to it says what that number is worth:
"confirmed on Wed. at 11:58 · 3 d 4 h ago". `live`, `stale` and `unknown` therefore look
**identical**; a separate drawing is reserved for `inferred_reset` (an inference, not a
measurement) and for a series that was **never** measured at all — there an empty track would
read as zero. It is the same model on both sides of the desk: `frontend/src/lib/freshness.ts`
and `panel/panel/view.py` are one decision in two languages, and the panel had it first.

The withdrawn meter (`unavailableReason`) **applies this rule, it does not break it**: when an
organization cuts off credits, Anthropic reports `percent: 0` — and that zero is rejected, so the
screen keeps the last MEASURED percentage together with the amounts, stamped with that earlier
measurement. The phantom is the zero from the withdrawal payload, never a value measured earlier.

**The deliberate mismatch between the web UI and the panel** lives in one place and follows from
screen size: the web UI shows, at the hard block, "credits disabled by the organization"; the
panel writes **nothing** about it — 480x320 has neither the room nor the need, so the credits row
shows only the amounts. That is a decision, not debt: the reason is an explanation, and the panel
is an indicator.

**5. No hardcoded bucket names.**
The response has 17 top-level keys, 5 of which were known from neither the validator inside the
Claude Code binary nor the reference repo. Series are **rows in a table**. A new bucket on
Anthropic's side has to work without a migration and without a deploy.

**6. The raw response always lands in the database.**
`raw_payloads` (content-addressed). It's the only way to reconstruct history after Anthropic
changes the schema. The reference repo lost user data because it had a strict decoder (PR #271).

**7. Account identity comes solely from `oauthAccount.accountUuid`.**
Never from static configuration. On one machine, accounts are switched through `/login`, and
`settings.json` is shared — a label would attribute half the samples to the wrong account and
silently poison both accounts' history, with no visible symptom at all.

**8. The API contract is frozen.**
`/api/status` returns `contractVersion` (today **3**). A breaking change means bumping the
version **and** updating `docs/API.md` **and** the `CONTRACT_VERSION` constant in
`frontend/src/api/types.ts` — the UI compares them and complains in the header on a mismatch.

This constant has **two** consumers: `/api/status` and `/api/stream` frames, because the
`account` frame carries the same `AccountStatus` model. Embed it in frames **verbatim** — a
"lite" variant would be a second contract to maintain, and that is exactly what would break this
rule. One function builds the account card (`build_account_status`), and
`test_status_contract.py` compares the result of both paths field by field.

A **third** consumer keeps its own copy of the constant: `panel/panel/model.py`
(`CONTRACT_VERSION`). The panel only reads the stream, so it only sees a mismatch in the `hello`
frame — and at that point it stops trusting the data instead of continuing to draw it.

**9. Compare `resets_at` with a tolerance, never for equality.**
The window boundary Anthropic reports **wobbles**: measured 49 samples over 3 h, one window,
values ranging from `00:59:59.014384` to `01:00:00.982268`. A literal comparison was always
false and silently disabled **three** mechanisms at once — dedup, the monotonicity guard, and
reset-boundary detection (61 "resets" a day instead of five). There is `parsing.same_reset_window`
and regression tests for this; a real reset moves the boundary by a whole window, so the two are
told apart with a threshold, not with equality.

**10. Time comes in through `NaiveUtcDt`, goes out through `UtcDt`.**
Ever since the output started carrying a zone, the browser **sends it back** (`Date.toISOString()`), while
the database, `utcnow()` and the samples are naive UTC. A plain `datetime` in a query parameter
lets a zoned timestamp in, and it doesn't fall over until a layer further down — History was
returning 500 on every open. The worse variant is silent: the MySQL driver formats the datetime
with `strftime` and **ignores** `tzinfo`, so a `+02:00` offset would shift the whole range by two
hours while still returning HTTP 200 and a correctly-shaped response. Both types sit side by side
in `app/schemas.py`.

**11. Do not describe the tree from memory — ask it.**
Before writing a sentence that names a file, a string or a line number, open it. A `file.py:NNN`
citation moves with its target or it is deleted; in range is not correct. A grep is a floor: it
finds the shape you already knew, so a clean sweep is not a completion signal, and a count taken
from a truncated window is not a count.

## Terminology

The project holds to a fixed vocabulary: window, pool, series, sample, freshness, cascade,
withdrawn meter, block, permission, credits, overage, reset, probe, frame, banner, card. Series
labels, warnings and captions reach the screen in those words. `display_label` is refreshed on
every ingest, so a wording fix reaches series registered earlier — a label is never frozen at the
moment its series was first seen. Four of the words are easy to reach for in the wrong place, so
each one carries the test that settles it.

**meter vs counter vs count vs countdown.** A **meter** is only the usage meter an organization
can withdraw — the thing `meter_withdrawn` names, that goes dark, works, tells the truth, has a
threshold. For anything else use the ordinary word: **counter** for a tally that increments
(`seen_count`, a loop or retry counter), **count** for a plain number of things, **countdown**
for time running down. The test: if "withdrawn" could not be said of it, it is not a meter.

**banner vs band.** A **banner** is the panel's strip across the top of a card — the thing that
is drawn. **Band** is the ordinary in-band / out-of-band sense: data carried inside the payload
itself, as against a signal arriving on a side channel (`spend.disabled_reason`, the client
cache). The test: if "out of" could be said of it, it is a band. Six sites in the backend, the
probe and the frontend use it that way, and "in-banner data" is not English.

**`ALLOW` on the panel is a width constraint, not a synonym.** The concept is **permission**,
matching the `permission` reason key; the panel's `SHORT` display value is `ALLOW` because that
is what fits: measured 39 px against `AlertList.REASON_W = 58`, where `PERMISSION` is 66 px and
`APPROVAL` 59 px and both overflow. Any longer string silently runs off the alert list — measure
before you change it.

**frame, qualified.** A stream frame and a display frame are different things; say which one you
mean wherever the surrounding text does not.

## Locale

| Item | Rule |
|---|---|
| Numeric date | day first, dotted — `26.07` |
| Clock | 24 h |
| Decimal separator | a **dot**, in prose and in the formatters alike |
| Time preposition | `at`, `yesterday at`, `on Wed. at`; the bare numeric-date form takes no preposition |
| `DAYS` | Sun. Mon. Tue. Wed. Thu. Fri. Sat. |
| HTML | `<html lang="en">` |
| Spelling | US (organization, utilization, color), to match the API payload |

## Layout

```
client/     probe (stdlib-only) + analysis tools; rules 1-3
              usage-probe.py — SOURCE OF TRUTH, the copies on machines are a release
                ALSO contains the blocked-session signaller (the "alert" section):
                measured 2.3 ms to fold it into this process versus 37.4 ms
                for a separate one — see `client/README.md`
backend/    FastAPI + async SQLAlchemy + Alembic; MariaDB; also serves the UI's static files
  app/parsing.py     pure functions, all normalization logic  <- start here for API changes
  app/freshness.py   pure functions, the four freshness states
  app/services/      ingest (write), status (read), cascade (limit rungs)
  app/services/events.py  the SSE broker — IN-PROCESS, see the --workers pitfall below
  app/sso.py         the gate: AUTH_MODE none / header / verify + an address allowlist
  app/main.py        mounts /assets + SPA fallback; the HTML shell is deliberately gate-free
frontend/   React 18 + Vite + TypeScript, no Tailwind and no charting library
  src/lib/freshness.ts   state -> appearance; the ONLY place this decision is made (rule 4 in pixels)
  src/lib/time.ts        stamp/atStamp — the ONLY place the "add the day or not" decision is made
  src/mocks/             VITE_MOCKS=1: states that cannot be triggered against a live deployment
docs/       API.md (the contract), RUNBOOK.md (operations and diagnostics)
              PANEL-ALERT-HANDOUT.md + handout/ — the built design for the alert card
              screenshots/ — the web UI images in README.md, rendered from VITE_MOCKS
deploy/     Apache vhost template; the secret is substituted at deploy time, NOT in the repo
```

**The frontend is built inside the backend's Dockerfile** (the `node` stage), so the build
context is the repo root, not `./backend`. Without `.dockerignore`, every build would ship the
whole `data/` directory, database included, to the daemon.

Iterating on the look: `cd frontend && VITE_MOCKS=1 npm run dev`. The backend has no CORS, so
running dev against a remote deployment would not work anyway — the mocks also give you states
that cannot be triggered against a live deployment. Variants via `?mock=states`.

## Tests

```bash
cd backend && pytest                     # env vars are set by tests/conftest.py
cd ../frontend && npm run typecheck      # the contract is typed, use it
cd ../panel && pytest
```

`tests/conftest.py` sets `AUTH_MODE`, `DATABASE_URL`, `INGEST_TOKENS` and `ALLOWED_EMAILS`
**unconditionally**, not through `setdefault` — otherwise a stray variable in the shell would
decide what the suite checks, and the result would drift between machines with no trace in the
repo.

The normalizer and the write path are tested against a **real payload** from a Max account
(`tests/fixtures/usage_max.json`). When you change `parsing.py` or `services/ingest.py`, update
the fixture with a fresh response — don't make up data.

## Deploy

```bash
git pull && docker compose up -d --build
```

`AUTH_MODE` is required and has no default — without it the container will not come up.
The configuration for one particular deployment (networks, ports, the identity service's
address) lives in the untracked `docker-compose.override.yml` and `.env`, not in the repo.

**The probe never needs to be copied.** Under the hooks path
(`%LOCALAPPDATA%\claude-usage-monitor\usage-probe.py`) a dozen or so lines of redirection are
enough; `runpy` executes the real file from `SRC` — that is, `client/usage-probe.py` in the
repo. That way an edit takes effect immediately, without `Copy-Item`. A symbolic link was meant
to go there instead; Windows refuses to create one without developer mode or administrator
rights. Details in `client/README.md`.

**Wherever a copy does live, it is a release.** `client/usage-probe.py` is the source
(`backend/tests/test_probe_parsing.py` loads it by a fixed path); the copy on a remote machine
**may be older** than HEAD, and that is correct — publishing is meant to be a decision, not a
side effect of a push. That's why **every change to the probe's behavior requires bumping
`SCRIPT_VERSION`**: the version rides along in every batch and is the only way to read from
`/api/machines` which machine is running which code. Without the bump, two different probes are
indistinguishable, and the question "why does that machine report differently" is left
unanswered.

## Pitfalls

- **`uvicorn --workers > 1` breaks the SSE stream.** The broker (`app/services/events.py`) lives
  in the process's memory, so with multiple workers an ingest can land in a different process
  than a client's connection, and **some subscribers go silent with not a single symptom in the
  logs** — data keeps flowing, just not to everyone. `entrypoint.sh` deliberately starts a
  single process. Scaling out requires moving the broker out of process first (Redis pub/sub, or
  `LISTEN/NOTIFY`), not just the flag.
- **The backend needs TWO networks.** `claude-usage-monitor_internal` has `internal: true`, and
  that also cuts **outbound** traffic — without a second network, `AUTH_MODE=verify` would have
  no way to reach the identity service, and the published port would have nothing to publish.
  If `docker compose up` ends with *"all predefined address pools have been fully subnetted"*,
  the host machine has exhausted Docker's address pools; look for orphaned networks, or attach
  the backend to a network that already exists (`networks: !override` in the override file).
- **`statusLine` does not work in the VS Code extension** — it's a CLI/TUI feature. Don't try
  going back to that idea. Issue
  [#55643](https://github.com/anthropics/claude-code/issues/55643) was closed as
  `not_planned` (by the inactivity bot), so this will not fix itself.
- **`claude -p "/usage"` does NOT use up quota, but `claude -p "whatever else"` does.**
  `/usage` is registered twice, and the `supportsNonInteractive` variant returns
  `{type:"text"}`, which sets `shouldQuery=false` — measured `num_turns=0`,
  `duration_api_ms=0`, `total_cost_usd=0`. If the argument doesn't match a local command, it
  turns into a **normal model turn**. The probe detects this via `num_turns>0` and discards
  such a dump.
- **Git Bash eats arguments that start with `/`.** `claude -p "/usage"` in Git Bash becomes
  `claude -p "C:/Program Files/Git/usage"` (MSYS path conversion), and instead of the local
  command you get a paid model turn. Test from PowerShell, or set `MSYS_NO_PATHCONV=1`. This
  cost two accidental calls at ~$0.10 each.
- **The hook payload is UTF-8, but `sys.stdin` decodes it with the locale encoding.** In the
  hook process, measured `sys.stdin.encoding = cp1250`, `errors = surrogateescape` — meaning
  `sys.stdin.read()` silently corrupts accented characters (one character becomes two: `ć` →
  `Ä‡`), and turns bytes with no cp1250 counterpart (`0x81 0x83 0x88 0x90 0x98`, among them `Ł`
  and the typographic apostrophe) into lone surrogates. Those don't fall over until a layer
  further down, at `.encode("utf-8")` inside `write_excl` — and because that call sits under
  `except Exception: pass`, the block entry came out **empty** and the alert vanished without a
  trace. Read `sys.stdin.buffer` and decode explicitly. The rest of the path has explicit utf-8
  throughout, so it faithfully carried whatever came in: one single spot was corrupting the
  toast, the panel and the backend all at once. The symptom is often **no event at all**, not
  garbled text.
- **PowerShell 5.1 reads a BOM-less `.ps1` as ANSI** — and then an em dash `—` (U+2014, three
  bytes in UTF-8) falls apart into three characters, one of which, `0x94`, is a **closing
  quotation mark**, U+201D. The parser takes it for the end of a string and fails dozens of
  lines further down, at a spot unrelated to the cause ("The string is missing the
  terminator"). We keep `.ps1` scripts in **pure ASCII** — that's more robust than counting on
  every future edit to preserve the BOM.
- **The client verifies TLS through `certifi`, not the system store.** The Windows CA store can
  reject a valid Let's Encrypt chain in which every link is valid. Don't disable verification —
  swap the store.
- **Windows refuses to create symbolic links** without developer mode or administrator rights
  (`Administrator privilege required`). That's why a dozen or so lines of Python redirection sit
  under the hooks path instead of a symlink.
- **The probe must have LF line endings, and shell scripts additionally need the `+x` bit.**
  The executable bit from git's index doesn't always carry over to the working tree — if the
  repo is viewed through a network share, git on the Linux side **silently skips** a script
  without `+x`. Line endings are enforced by `.gitattributes`; when working from such a share,
  also set `core.filemode=false`, otherwise `git add -A` from Windows will strip the executable
  bit without a word of warning.
