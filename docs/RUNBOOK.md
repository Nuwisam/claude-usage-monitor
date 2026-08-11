# Runbook

Deployment directory in the examples: `/var/lib/claude-usage-monitor`. Substitute your own
addresses and paths — `usage.example.org` and `192.0.2.10` are placeholders here, nothing more.

The UI is served by **the same backend container** (static files from the `node` stage in
`backend/Dockerfile`), so there is no separate frontend container and nothing to restart on its own.

## Daily operation

```bash
cd /var/lib/claude-usage-monitor
docker compose ps
docker compose logs -f --tail 50 claude_usage_monitor_backend
git pull && docker compose up -d --build      # regular deploy
```

## Is the system alive

```bash
# 1) containers
docker compose ps                       # both healthy

# 2) whether data is growing (run twice, a few minutes apart, WHILE WORKING with Claude Code)
DB=$(grep -oP '^MARIADB_DATABASE=\K.*' .env); RP=$(grep -oP '^MARIADB_ROOT_PASSWORD=\K.*' .env)
docker exec claude_usage_monitor_mariadb mariadb -uroot -p"$RP" "$DB" -N -e \
 "select concat('batches=',count(*),' samples=',(select count(*) from limit_samples),
   ' last: ',timestampdiff(second,max(received_at),utc_timestamp()),' s ago')
  from ingest_batches;"

# 3) whether measurements are FRESH, not just present
docker exec claude_usage_monitor_mariadb mariadb -uroot -p"$RP" "$DB" -N -e \
 "select measurement_source, count(*), round(avg(cache_age_s)), round(avg(fresh_age_s))
  from ingest_batches where received_at > utc_timestamp() - interval 1 hour
  group by measurement_source;"
```

There are no HTTP codes here and there will not be — since version 3 the probe sends no request
to Anthropic at all. The query above guards something else, and something more important: **`cli_merged`
means fresh percentages came in from `/usage` stdout, and `cli_usage_cache` means only Claude
Code's own cache went out, which can be up to 5 minutes old.** The danger of the latter is a
silent failure: data keeps flowing, the chart looks normal, only the resolution dropped from a
minute to five. The most common cause is no `claude` in the `PATH` of the hook process — check
`spawn_error` in the local log.

**A note on reading this:** data grows only while you are **working** with Claude Code, and
only once every 60 s (the throttle). `PostToolUse` fires **after** a tool finishes, so nothing
arrives during one long call — that is correct behavior, not a failure.

## Diagnostics on the client side

```bash
python client/analyze-samples.py        # rate of change, errors, account switches
```

Local log: `%LOCALAPPDATA%\claude-usage-monitor\usage-samples.jsonl`.
If the log grows but the database does not, the problem is with sending; check `spool.jsonl`
(grows on failed POSTs), repeat the handshake from
[`client/README.md`](../client/README.md#3-handshake--we-check-the-secrets-before-touching-settingsjson)
and compare the status against the 401/403 rows in "Common problems" below.

## Common problems

| Symptom | Cause and what to do |
|---|---|
| Container will not come up, log shows an `AUTH_MODE` validation error | Variable unset or empty. Compose substitutes an **empty string** for an unset variable, and the mode is a literal match — enter `none`, `header` or `verify` in lowercase |
| `/api/status` returns **503** `sso-unreachable` | Only under `AUTH_MODE=verify`. The backend cannot reach the address in `AUTH_VERIFY_URL` — check the address and whether the backend has a network path to it (`internal: true` also cuts outbound traffic) |
| `/api/status` returns **503** `sso-unavailable` | `AUTH_MODE=verify` with an empty `AUTH_VERIFY_URL`, or the identity service answered with a status other than 200/401/403 |
| `/api/status` returns **401** `not-authenticated`, the UI says "you are not logged in" | Under `header`, the proxy did not supply the header named by `AUTH_EMAIL_HEADER`; under `verify`, there is no session. A missing `redirect_url` in the response means `AUTH_LOGIN_URL` is empty — the UI does not guess the login address |
| `/api/status` returns **403** `email-not-allowed` | `ALLOWED_EMAILS` in `.env` does not contain your address |
| Ingest returns **403**, and the token is good | Missing `X-Ingest-Key` header, or a mismatch with `INGEST_EDGE_KEY` in the Apache vhost |
| Ingest returns **401** | Wrong machine token. Compare the client's `config.json` against `INGEST_TOKENS` |
| `samplesWritten: 0`, but `ok: true` | **Correct.** Dedup — the values did not change and the heartbeat (5 min) has not elapsed. The measurement still bumps `last_confirmed_at`, so the UI shows it as fresh |
| Series `live`, but `capturedAt` is hours old | **Correct and intentional.** `capturedAt` is the last SAMPLE written, freshness is computed from `confirmedAt`. The value simply has not changed — the UI captions this `unchanged since` |
| All series suddenly `stale` | No confirmation for 5 min. Since v3 **this is no longer a dedup artifact**, but it need not be a failure either: hooks only fire while you work, so a fifteen-minute break gives the same symptom. **You will not see this in the UI** — `stale` looks like `live`, only the reading-age label grows |
| A series in state `unknown` in `/api/status` | The client is reporting, but there are no samples for this series. **Do not ignore it** — this is the only state that means a failure. **The UI does not name it and no longer has a banner for it**: it shows the last measured percentage with a growing reading age, so you see the failure by the age growing despite work happening. To confirm: `curl -s -b "$COOKIE" .../api/status \| jq '.accounts[].series[] \| select(.freshness=="unknown") \| {label, rawUtilization, confirmedAt}'`, then `events` and the local log |
| The UI shows a number, but the probe has been gone for days | **Correct.** The value is the last MEASUREMENT, not a guess, and its age is shown next to it (`confirmed on Wed. at 11:58 · 3 d 4 h ago`). Zero would be a lie here — rule 4 in `AGENTS.md` |
| `docker compose up` → *"all predefined address pools have been fully subnetted"* | Docker has exhausted its pools (a limit of ~31 networks). Remove an orphaned network: `docker network ls`, check `docker network inspect <n> -f '{{len .Containers}}'` |
| `clock_skew` events | The client's clock has drifted >5 min from the server's. **Pure diagnostics — it does not affect the write.** The measurement is dated by the server (`received_at` minus the age), and the age is a difference measured entirely on the client's own clock, so the drift does not corrupt it. Still worth syncing the clock |
| `clock_backwards` events | The measurement is dated **after** `sent_at`, meaning the client's clock ran backwards between writing and sending. The whole entry is rejected (the raw payload is in `raw_payloads`), because otherwise it would land on the receive time and overwrite state with a stale reading. Repeated occurrences = broken time sync on that machine |
| `no_captured_at` events | A payload with no measurement time. The observation is skipped — we do **not** substitute "now", because that would turn ignorance into confident-looking freshness |
| `schema_drift` events | Anthropic changed the shape of the response. The payload is saved in full; look at `GET /api/batches/{id}/raw` and update `app/parsing.py` + the fixture |
| UI returns **404** on `/claude-usage/` | The image was built without the `node` stage, or `dist` is empty. `docker exec claude_usage_monitor_backend ls /app/static` — should contain `index.html` and `assets/`. If empty: `docker compose build --no-cache` |
| UI loads, but a **blank page** and a console error | Vite's `base` is out of step with the Apache rule. Assets must hang off `/claude-usage/assets/…`; `APP_BASE_PATH` in `.env`, `VITE_BASE_PATH` in the build arg and `ProxyPass` must all say the same thing |
| The UI header shows **`contract vN ≠ vM`** | Backend and frontend drifted apart on contract version. First check whether `CONTRACT_VERSION` in `app/services/status.py` and in `frontend/src/api/types.ts` are **equal in code** — bumping only the backend produces exactly this symptom with otherwise correct data. Only then `docker compose up -d --build` (different commits in the image) |
| History returns **500**, log shows `can't subtract offset-naive and offset-aware datetimes` | The `from`/`to` parameter arrived with a zone (the browser sends `toISOString()`), and the backend computes on naive UTC. Date parameters must be typed `NaiveUtcDt` from `app/schemas.py`, not `datetime` |
| **The sample count grows on every measurement** despite no changes | Broken dedup. Check whether the `resets_at` comparison goes through `same_reset_window` (tolerance), not equality — the window boundary wobbles by ~2 s, and a literal comparison disables dedup, the monotonicity guard and reset-boundary detection all at once |
| `POST /api/session-alert` returns **403** (HTML from Apache) | The vhost is missing the `<Location /claude-usage/api/session-alert>` block with the `X-Ingest-Key` filter. Ingest works because it has its own block — the two paths have separate rules. Template: `deploy/apache/claude-usage-monitor-include.conf.example` |
| **A card or marker on the panel will not go dark** | A stuck state entry, because a denial and Esc generate no hook event at all. Four cases, separately: (1) **the session is no longer alive** — it goes dark at the next `UserPromptSubmit`/`Stop` of **any** session on that machine, because its record is no longer in `~/.claude/sessions/`; (2) **the session is alive but fell silent after a denial** — it goes dark the same way, only the evidence is a `tool_result` in its transcript; (3) a call whose `tool_use` record does not fit in the 32 KB tail (a large `Write`, ~1.3%) — it is left with a 24 h TTL; (4) the **last** session on the machine was killed (nobody sweeps the registry) or it was WSL — also a TTL. When you see `alert_skip` in the probe's log, the mechanism deliberately held back and the reason is on that same line. On demand: `del %LOCALAPPDATA%\claude-usage-monitor\session-status\*` — the panel goes dark at the next event of any session, because the probe reconciles the set with the marker then — or `"session_status": false` in `config.json`, which sends an empty set right away. **Exception: after a backend restart** an ongoing block will not return to the panel until the next change of the set on that machine; this is a known limitation, documented in `docs/API.md` § 3.2 |
| **Toasts pop up on a freshly set-up machine**, though nothing was configured | Intentional: the signaller works even without `config.json` — it writes state files and raises notifications, only the send stays silent. `"session_status": false` turns it off, `"toast": false` mutes just the toast |
| **The alert does not arrive, though the measurement does** | Check in order: `alert_url` in `config.json`, the Apache block (row above), then whether the panel has `"session_alerts": true` in `panel.json`. These are two separate flags on two separate machines: `session_status` kills the SOURCE, `session_alerts` kills the DISPLAY |

## Backup

**The host's backup probably does NOT cover this database.** A typical `mysqldump --all-databases`
connects to the host's native MariaDB (`127.0.0.1:3306`), and this one runs inside a container
and does not expose a port outward. Check this before assuming you have a copy.

What is at stake is **usage history** — irreplaceable, because Anthropic does not expose the past.
The current state rebuilds itself at the first measurement.

```bash
./scripts/backup.sh                              # dump into backups/, 14-day retention
./scripts/restore.sh backups/claude_usage_*.sql.gz   # restores into the scratch database BY DEFAULT
TARGET=prod ./scripts/restore.sh <file>          # overwrites production, asks for confirmation (type YES)
```

`restore.sh` deliberately restores into the `*_scratch` database. A backup that has never
been restored is not a backup — and restoring "just to try it" straight onto production is the
best way to lose it.

To turn on the automatic run, add one line to a weekly cron:
```
/var/lib/claude-usage-monitor/scripts/backup.sh
```

## Changing the client on a machine

First installation on a new machine: [`client/README.md`](../client/README.md#installation).

**Nothing needs copying** if the path under the hooks holds a redirection that runs
`client/usage-probe.py` straight from the repo — then an edit takes effect on the next run and
nothing needs restarting, because the hook reads the script and `config.json` on every run.
Just remember that **the first run does not measure**: the probe does not wait for
`claude -p "/usage"`, the next cycle consumes the result.

When a remote machine holds a COPY of the probe, not a redirection, watch `SCRIPT_VERSION`.
The version rides in every batch and is the only way to read from `/api/machines` which
machine is running which code — without a bump, two different probes are indistinguishable.

## Turning off collection

```powershell
# one machine: delete or rename config.json  -> stops SENDING
Rename-Item "$env:LOCALAPPDATA\claude-usage-monitor\config.json" config.json.off
# or entirely: remove all TWELVE probe blocks from ~/.claude/settings.json
```

**Removing `PostToolUse` and `Stop` is not enough** — after merging with the signaller the
probe hangs off twelve events (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
`PostToolUseFailure`, `PostToolBatch`, `SubagentStop`, `Stop`, `Notification[idle_prompt]`,
`PermissionRequest`, `PermissionDenied`, `SessionEnd`), and after removing two of them it is
still triggered by the remaining ten.

**Renaming `config.json` does not silence the toasts.** The blocked-session signaller by
default works even without configuration: it writes state files and raises Windows
notifications, only the send stays silent. To silence just the alert, without touching the
measurement:

```powershell
# in config.json:  "session_status": false
```

Turning it off also **clears whatever is currently outstanding** — on the first event it drops
the entries and sends one empty set, so the marker disappears from the panel at once, not after
a day.

On the server side, revoking a single machine means removing its entry from `INGEST_TOKENS`
in `.env` + `docker compose up -d`. **Not `restart`** — `INGEST_TOKENS` is read when the
container is created, so after a `restart` a revoked token still works. The reverse direction
(issuing a token to a new machine) is in
[`client/README.md`](../client/README.md#1-machine-token--this-step-happens-on-the-server).

## SSE stream client (AX206 panel, scripts)

The browser needs nothing — `EventSource` rides on the session cookie. A client without a
session needs its own token and **account UUIDs**, because the subscription is by UUID, not
by email address (one address can point at several accounts, and email gets overwritten).

Note: the panel **always** sends an `Authorization` header, so with `STREAM_TOKENS` empty it
gets a 401 regardless of `AUTH_MODE` — even under `none`.

```bash
# 1) token — SEPARATE from INGEST_TOKENS, the latter is write-only
openssl rand -hex 32
# add to .env:  STREAM_TOKENS=<hex>:panel      (then: docker compose up -d)

# 2) UUIDs of the accounts the panel should be pinned to (from a browser with a valid session)
curl -s $B/api/accounts | jq -r '.[] | "\(.uuid)  \(.email)"'

# 3) a live try — hello + snapshot at once, then a ping every 15 s
curl -N -H "Authorization: Bearer $STREAM_TOKEN" \
     "$B/api/stream?account=<uuid>&account=<uuid>"
```

If `hello` returns a UUID in the `unknown[]` field, it is either a typo or the account has not
reported yet — the connection stays open and frames will start arriving once the account shows up.

**After `/login` on a new account the panel needs its UUID added.** The browser finds out on
its own (polling `/status` every 3 min switches the stream), the panel does not — because
nobody asked for that UUID.

**Frames arrive in clumps every few dozen seconds instead of at once?** That is Apache
buffering: check whether the `/claude-usage/api/stream` rule comes **before** the generic
`/claude-usage/api` one and has `SetEnv no-gzip 1`.

**Writing your own stream client?** Read from the socket with `read1()`, not `read(n)` — the
latter waits until it has ALL `n` bytes, so after the first chunk the following cards and
pings sit in the buffer, and the client sits stuck on the first frame **looking alive**. The same
trap caught the panel and cost us a long hunt for "where did the second frame go".

## The AX206 panel on a desk

Client: `panel/` in this repo, hardware details and diagnostics in `panel/README.md`.

```powershell
cd <repo>\panel
python -m panel --list                 # which screens are visible and on what ports
python -m panel --probe                # test card: colors, bars, descenders
python -m panel --once                 # one frame of real data, then exit
.\deploy\install-task.ps1              # venv outside the repo + task on logon
```

`install-task.ps1` is idempotent: it stops the previous instance (waiting for the **process to
die**, not for the task's state — otherwise the new one runs into a `panel.lock` that is still
held and quietly exits, and the old code keeps drawing), registers the task, **starts it**, and
waits
until the log shows `first frame after opening`. Registration alone starts nothing: the trigger
is on logon, so an installation on an already logged-on session would leave the screen dark
with no trace of why. When you see `PANEL BUSY` instead of confirmation, another program holds
the module — stop that program, and the client will pick up drawing on its own within ~30 s, no
reinstall needed.

Configuration: `%LOCALAPPDATA%\claude-usage-monitor\panel.json` — a **separate file** from the
probe's `config.json`, because the stream token has a different scope than the ingest token.
Accounts are two named fields (`account_1`, `account_2`), not a list — the screen layout has
exactly two bands.

**The handle to the module is exclusive: either the panel or the other program.** As long as
there is one display, the other program has to give it up. Once a second one is added, both
programs run side by side, but then **each has to be pinned to a specific unit** — both share
the same serial number `WCH32` (a firmware constant), and Windows derives both the instance ID
and the `ContainerID` from it, so those values will also be identical. Only the port chain from
libusb-1.0 tells them apart (`"panels": [{"backend": "ax206", "port_path": "3.4"}]`; the old,
single-screen shape `"device": {…}` is still migrated). The old `"location":
"Port_#0004.Hub_#0005"` now gives a configuration error: the `Hub_#` part was an enumeration
counter and could jump without touching the plug. Confirm which module is which with
`--identify`.

### Debt: the panel's captions drifted from the web

**Time labels are already unified.** `panel/panel/fmt.py` ports `at_stamp()` and the day-level step
in `ago()`, `DAYS` is exactly the table from `time.ts` (indexed from Sunday via `_day_index()`,
because `weekday()` counts from Monday), and the manual `with_day` flag from `view.py` is gone
— one place decides whether to add the day, same as on the web. Panel and web now write
`· on Fri. at 20:00` and `3 d 4 h ago` the same way.

What used to be left: the docstring of `panel/panel/view.py` claimed that "the web tells `live`
from `stale` by fill color and gives `unknown` its own drawing" — that claim has since been
fixed in the code, not just noted here. The docstring now says the opposite and more precisely:
the web has no separate freshness drawing either, both sides carry recency on the reading-age
label alone, and the one real difference is narrower — the web can say `no meter` for a
withdrawn meter (`unavailableReason`), while the panel, having no such field, can only say
`unknown`.

The rest of the panel/web differences are **deliberate**, not debt: 480x320 shows less, and
shows it for less time. The panel has no `no meter` words, no `inferred_reset` drawing, no
"confirmed …" caption and no per-series age. There is also no shared guard for caption parity —
changes in `time.ts` have to be ported by hand, and `test_fmt_port.py` only catches what it has
been told to expect.

## First deployment from scratch

Behind a reverse proxy. A local install is the same `AUTH_MODE=none` and `docker compose up -d
--build` — the rest of this section does not apply to it.

```bash
cd /var/lib/claude-usage-monitor
cp .env.example .env            # MARIADB_*, AUTH_MODE, INGEST_TOKENS, INGEST_EDGE_KEY
                                 # ALLOWED_EMAILS; STREAM_TOKENS only for a client without a session

# the proxy reaches the container over the Docker network, so the port must NOT be published
cat > docker-compose.override.yml <<'EOF'
services:
  claude_usage_monitor_backend:
    ports: !reset []
EOF

docker compose up -d --build

EDGE=$(grep -oP '^INGEST_EDGE_KEY=\K.*' .env)
sed "s|__INGEST_EDGE_KEY__|$EDGE|" deploy/apache/claude-usage-monitor-include.conf.example \
    > /etc/apache2/sites-available/claude-usage-monitor-include.conf
chmod 600 /etc/apache2/sites-available/claude-usage-monitor-include.conf
# add to sites-available/example_org-ssl.conf:
#   Include sites-available/claude-usage-monitor-include.conf
apachectl configtest && systemctl reload apache2
```

API verification: 401 on `/api/status` without a session (with `redirect_url`, if you set
`AUTH_LOGIN_URL`), 403 on `/api/ingest` without `X-Ingest-Key`, 401 with the edge key but no
token, 200 with the full set. Also check that `/openapi.json` and `/docs` do **not** hand back
the schema — look at the body, not the status code, because the SPA fallback answers those
paths with `index.html` and a 200.

UI verification (none of it needs a session):
```bash
B=https://usage.example.org/claude-usage
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' $B/          # 200 text/html
curl -s -o /dev/null -w '%{http_code}\n' $B/history                   # 200 -- SPA fallback
curl -s $B/api/doesnotexist                                           # 404 JSON, NOT index.html
curl -sI $B | grep -i location                                        # 301 to /claude-usage/
```

The last one matters: the SPA fallback catches everything the routers did not match, so a typo
in an endpoint address can start serving an HTML page instead of an error. `backend/tests/test_static_spa.py`
guards this.
