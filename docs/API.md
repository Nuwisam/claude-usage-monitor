# API Contract

All examples are **generated from the running system**, not invented. Identities and amounts
are substituted; the shape of the response, the percentages and the relationships between
fields are not.

The API base URL is `<origin><APP_BASE_PATH>/api`, which under a default local install is
`http://127.0.0.1:8080/claude-usage/api`. In the examples below: `https://usage.example.org/claude-usage/api`.

**The OpenAPI schema is disabled** (`/openapi.json`, `/docs`, `/redoc`). The reverse proxy
passes through the whole container root, and the authorization gate sits on the endpoint
dependencies, not on those routes — an exposed schema would hand the full set of paths and
field names to anyone with no session. This file is the only description of the contract.

**`contractVersion` = 3.** What v3 added:

- **`confirmedAt`** — when this value was last CONFIRMED. **Freshness is computed from this
  field, not from `capturedAt`.** Dedup deliberately writes no sample when the value has not
  changed, so `capturedAt` can be minutes older than the last real measurement — and then a
  stable reading looks in the UI like a broken connection. Those are two opposite pieces of
  information for someone who is right now deciding whether to kick off a big task.
- **`valueSince`** — since when the value has been unchanged. This is where the "unchanged
  since 12:05" caption comes from.
- `capturedAt` stays, and still means the same thing: the time of the last SAMPLE written.
- **`deltaFrom`** — the SAMPLE from which `deltaPct1h` is counted. The baseline is trimmed to
  the **current window**, so the span is sometimes shorter than an hour and the UI has to say
  so ("+3 pp since 14:03", not "+3 pp over the last hour"). Before this, after a session reset
  the reference point came from the previous window and "−46 pp over the last hour" hung on
  screen for an hour. `null` always and exactly when `deltaPct1h` is `null`. The name
  `deltaPct1h` stays — renaming it would break compatibility, and adding a field does not
  (see § 11).
- **`unavailableReason`** (series) and **`reason`** (cascade rung) — the reason a meter
  **cannot be read**, verbatim from Anthropic. When an organization exhausts its global spend
  ceiling, the endpoint does not stop responding — it **zeroes the meter**: `percent` drops
  from 91 to 0, `used` from EUR 273.15 to 0.00, `limit` and `cap` disappear. Without this
  field a hard block is **indistinguishable** from an account that never had credits, and the
  UI would draw a confident, measured 0% — meaning "you have the full 300 EUR" at the exact
  moment you have nothing. Only **`utilization`** disappears then (the current figure);
  `rawUtilization`, the amounts in `extra` and every timestamp describe the **last true
  measurement before the block**. The set of values **is open** (observed:
  `org_level_disabled_until`, `org_spend_cap_reached`), so branch on `!== null`, never on the
  content. The rung's `state` did **not** get a fourth value — withdrawn credits are still
  `off`.

What v2 added over v1, and why:

| Change | Reason |
|---|---|
| **Time on the wire with an offset** (`…Z`) — **the only breaking change** | v1 sent naive UTC with no zone, and `new Date("2026-07-26T19:07:37")` in JS is LOCAL time. The countdown was silently shifted by the zone |
| `kind`, `group`, `bucketKey` on series | Without them the UI had to guess which series was the 5-hour window — by `sortOrder` or by a key prefix, exactly what rule 5 of `AGENTS.md` forbids |
| `cascade[]` on the account | "Which rung of the limit you're on" is domain knowledge and requires reaching into untyped `spend`/`extra_usage`. The backend computes it, and it is tested |
| `gaps[].kind = no_samples` actually emitted | The field existed in v1, but **no line of code ever set it** — a broken series looked identical to idleness |
| `resets[]` independent of the bucket | In v1 it was only filled for `bucket=raw`, so the default 24-hour view came back without a single reset boundary |

---

## 1. What this system shows

How much **Claude limit is currently left**, for several accounts at once. Today two: one
**Max**, one **Team**. History accumulates along the way, but it is not the goal.

Data comes from a probe that runs on the user's machines while they work with Claude Code.
**It does not flow when you are not working** — and that is not a bug, just a property of the
source that the UI has to communicate honestly (see § 4).

---

## 2. Authentication

The backend is its own gate — there is no nginx with `auth_request` in front of it, so
**nobody returns a 302 to the browser**. Who the caller is gets decided by `AUTH_MODE`:

| `AUTH_MODE` | Identity comes from |
|---|---|
| `none` | nowhere — every request goes through. Only when the port is not reachable from the network |
| `header` | the `AUTH_EMAIL_HEADER` header, set by the proxy (which must **strip** it from incoming requests) |
| `verify` | the JSON response of an identity service at `AUTH_VERIFY_URL`; field names are configuration |

Regardless of mode (other than `none`), an `ALLOWED_EMAILS` allowlist sits on top:
authentication says WHO, the allowlist says WHO IS ALLOWED.

| Situation | Response |
|---|---|
| No session | `401 {"detail": {"reason": "not-authenticated"}}`, with `redirect_url` when there is somewhere to send it |
| Authenticated but off the allowlist | `403 {"detail": {"reason": "email-not-allowed"}}` |
| Identity service unreachable | `503 {"detail": {"reason": "sso-unreachable"}}` |
| `verify` with no `AUTH_VERIFY_URL`, or an unexpected status | `503 {"detail": {"reason": "sso-unavailable"}}` |

**On a 401 with `redirect_url` the UI redirects; on a 401 without one it shows an error in
place.** This is part of the contract, not an implementation detail. The UI **does not guess**
the login address — the backend knows whether it sits behind anything that logs in, the UI
does not; sending the user to a guessed, typical path ends in a 404 and looks like an
application failure.

`redirect_url` appears when the identity service supplies it (the field named by
`AUTH_REDIRECT_FIELD`) or when `AUTH_LOGIN_URL` is set. `{rd}` in that address gets replaced
with an encoded return address, assembled from `PUBLIC_ORIGIN` + `APP_BASE_PATH`:

```json
{"detail": {"reason": "not-authenticated",
  "redirect_url": "https://usage.example.org/oauth2/start?rd=https%3A%2F%2Fusage.example.org%2Fclaude-usage%2F"}}
```

403 and 503 **do not** redirect to login: a logged-in user sent to log in comes back and gets
the same refusal — a loop.

Every response carries `Cache-Control: no-store`. A stale limit percentage is worse than no
response.

---

## 3. Endpoints

| Method and path | Returns |
|---|---|
| `GET /me` | The current logged-in user: `{email, verifiedAt}` — from the SSO session |
| `GET /status` | **The main endpoint.** Current state of all accounts and series. Poll every **15 s**, and every **3 min** once the stream (§ 3.1) is connected |
| `GET /stream?account=<uuid>` | **SSE.** An account card pushed immediately after a measurement. See § 3.1 |
| `GET /history?account=&seriesId=&from=&to=&bucket=auto` | Progression over time + gaps + reset boundaries |
| `GET /accounts` | List of accounts. **`PATCH /accounts/{uuid}` DOES NOT EXIST** — the `label`, `color`, `isEnabled` columns are in the database, but there is no write path. Do not build editing on top of it |
| `GET /machines` | Which machines reported which accounts, with the **probe** version (`scriptVersion`) |
| `GET /series` | Series registry (see § 6 — the list is open) |
| `GET /events` | Operational log: account switches, schema drift, client errors |
| `GET /batches` · `GET /batches/{id}/raw` | Ingest log + Anthropic's raw response |
| `GET /stats` | Counters, dedup ratio, 24 h ingest success rate |
| `POST /ingest` | **This is what the probe writes to.** Per-machine Bearer, plus the `X-Ingest-Key` edge filter — the same pattern as § 3.2. Accepts one measurement plus a `backlog[]`; returns `{ok, samplesWritten, backlogAccepted, serverNow, batchId, seriesRegistered}` |
| `POST /session-alert` | **No SSO, per-machine Bearer.** Claude Code sessions that have stopped and are waiting on a human. See § 3.2 |

**`/batches` has no HTTP codes, and never will** — since v3 the probe sends no request to
Anthropic, so there is no response for them to describe. In their place is the provenance of
the measurement: `measurementSource` (`cli_merged` = fresh percentages from `/usage`'s stdout,
`cli_usage_cache` = the Claude Code cache alone, up to 5 min stale), `cacheAgeS`, `freshAgeS`.
A run of `cli_usage_cache` is a silent failure — data keeps flowing, only the resolution drops
from one minute to five, and **this is the only place where that is visible directly**.

**Time works both ways.** `from` and `to` in `/history` accept ISO-8601 with a zone (`…Z`,
`…+02:00`) or without one — with no zone UTC is assumed, with a zone the value is
**converted**, not truncated. Just send `Date.toISOString()`.

This is not a courtesy to the client, just tying up a loose end: once contract v2 attached a
zone to outgoing time, the browser started sending it back — and the History view returned
**500** on every open, because the rest of the backend counts on naive UTC.
`backend/tests/test_history_endpoint.py` guards this.

---

## 3.1 Event stream (SSE)

```
GET /api/stream?account=<uuid>&account=<uuid>[&snapshot=0]
Accept: text/event-stream
```

The server pushes a frame when a measurement for a **registered** account arrives through
`/api/ingest`.

**The subscription is exclusively by `account_uuid`.** There is no email address anywhere in
this contract — not as a parameter, not as a match key. One address really can point at
several accounts (a Pro account and a seat in a Team under the same address), and `email` gets
overwritten on every measurement; addressing by it would mean the set of accounts under a
subscription changes without the subscriber knowing — silently. At least one `account` is
**required**: a missing parameter is `400 {"reason": "no-subscription"}`, never an implicit
"everything".

**Authorization: Bearer, or the ordinary gate.** The presence of an `Authorization` header
selects the token path (`STREAM_TOKENS`); without one, `AUTH_MODE` applies as everywhere else,
so a browser's `EventSource` needs nothing extra. `STREAM_TOKENS` is a **separate secret** from
`INGEST_TOKENS` — the probe's token is a write-only credential and does not open reading.

A consequence for headless clients: since the path is chosen by the mere PRESENCE of the
header, a client that always sends it (like the AX206 panel) will get a 401 with an empty
`STREAM_TOKENS`, even under `AUTH_MODE=none`.

| event | when | content |
|---|---|---|
| `hello` | once, at start | `{contractVersion, serverNow, subscribed[], unknown[], pingSec, maxLifetimeSec}` |
| `account` | a snapshot at start + after every accepted measurement | `{contractVersion, serverNow, account, warnings[]}` |
| `ping` | every `pingSec` (15 s) | `{serverNow}` |
| `lag` | the receiver did not keep up | `{reason:"queue-overflow", dropped}` |
| `alert` | a snapshot at start + after every `POST /session-alert` | `{contractVersion, serverNow, alerts[]}` — see § 3.2 |
| `bye` | after `maxLifetimeSec` (900 s) | `{reason:"lifetime"}`, then a clean close |

**`account` carries exactly the same object as an element of `accounts[]` in `/status`** — the
same model, the same fields, the same server-side assembly function. There is no "lite"
variant and there will not be: a second shape for the same data is a second contract to
maintain.

Four things worth knowing before you hook this up:

1. **`unknown[]` is not an error.** These are UUIDs that are not in the database — a typo, or
   an account that has not been created yet. The connection stays open and the subscription
   still covers it, so an account created while the stream is running arrives on its own.
   We report it because a configuration mistake has to look different from idleness.
2. **There is no replay** and no `Last-Event-ID`. On reconnecting you get a fresh snapshot,
   which is strictly better than replaying history. Every frame is the **full** account state,
   not a delta — which is why losing frames is harmless and why `lag` is enough as the sole
   signal of an interruption.
3. **`bye` after 900 s is normal.** It is the only moment a long connection re-verifies the
   SSO session. `EventSource` resumes on its own; a headless client must resume itself.
4. **Polling stays.** The stream does not recompute freshness while the client is silent
   (because silence generates no events), and it will not show an account whose UUID nobody
   asked for. `/status` every **3 min** closes both gaps.

   Why 3 min and not one: the probe has a 60 s throttle, so a one-minute poll could not show
   anything the stream had not already delivered. What the poll is really for changes **with
   the passage of time**, not with new data.

   Now that freshness is carried by the age label, the `live → stale` transition **changes
   nothing in the UI** — the age is computed from `confirmedAt` against a locally ticking
   "now", so it grows correctly even when the poll brought back not a single byte. The poll's
   real job is the one transition that still **changes the drawing**:
   `stale → inferred_reset` once the window has rolled over. The worst case: a series shows
   the old figure for 3 minutes longer than it should be showing `~0` — and the old figure is
   higher than zero, so the error errs on the safe side.

`snapshot=0` skips the startup cards — used by the browser, which already fetched `/status`
before opening the stream.

**The filter is routing, not authorization.** A logged-in user sees every account in
`/status`; the stream shuts nothing off from anyone.

---

## 3.2 Blocked Claude Code sessions

A session that has stopped on a question to a human — a tool permission request,
`AskUserQuestion`, or `ExitPlanMode` (`reason`: `permission` | `question` | `plan`).
`client/usage-probe.py` signals it (the same probe, the "alert" section — measured at 1.7 ms
against 41.9 ms for a separate process), and the desk panel shows a card and a marker next to
the account. Off switch: `"session_status": false` in `config.json`.

**A session can run on a remote machine while the panel sits locally** — which is why events
go through the backend as a proxy. But **they are not in the database**: they live only in
process memory. A block clears the moment someone clicks "yes", so a table would mean a
migration and a row lifecycle for a state that is meant to leave no trace at all.

**A backend restart clears the map, and alerts do NOT come back on their own** — this used to
say they do, and that was not true. The probe sends its set when it diverges from its own
marker of the last thing sent; a server restart does not touch that marker, so from the
probe's point of view everything is already announced. An ongoing block will come back to the
panel only when the set **changes** on that machine (a new block, or one of the current ones
going away). Closing this gap requires the server to tell the probe its own state — e.g. in
the response to the once-a-minute measurement POST — and is a separate topic, not a property
of the current contract.

```
POST /api/session-alert
Authorization: Bearer <THIS machine's token, from INGEST_TOKENS>
X-Ingest-Key: <INGEST_EDGE_KEY>

{"entries": [
  {"key": "<session>__<agent|main>__<key>", "reason": "permission",
   "project": "claude-usage-monitor", "tool": "Bash", "detail": "git status",
   "since": "2026-08-05T21:00:00Z", "account_uuid": "…", "session_id": "…",
   "agent_id": null, "agent_type": null, "permission_mode": "default"}],
 "sent_at": "2026-08-05T21:00:01Z", "script_version": 11}

200 {"ok": true, "machine": "desktop", "accepted": 1, "subscribers": 1}
```

The probe sends **snake_case**, the same shape as the entry on disk — with one exception: the
**local** fields (`transcript_path`, `prompt_id`, `registry_seen`) get stripped by
`snapshot()` before sending. They exist purely to clear a block on the client side: the first
two for closing it out from the transcript, `registry_seen` for the death rule from the
harness's session registry. `transcript_path` incidentally carries the human's home directory
name. The models accept both forms (`populate_by_name`), so camelCase goes through too. On the
way out, in the `alert` frame, the fields are already camelCase like the rest of the contract.

Four things worth knowing:

1. **Every POST REPLACES its machine's set in full.** There are no increments, so there is no
   state to reconcile and no way for a lost "exit" to leave an orphan. An empty list clears
   that machine's alerts. Two overlapping requests from one machine give the same result as
   the later one alone — which is why the endpoint does not serialize writes and opens no
   transaction.
2. **`machine` is assigned by the server from the token, never by the client.** It is the
   label a human reads off the panel and uses to decide where to go.
3. **The `alert` frame carries the FULL current set from all machines**, just as `account`
   carries a full card. It goes to **every** stream subscriber, not only to those subscribed
   to some account: a block does not belong to any single account. The frame carries `machine`
   per entry, so a filter can be added later without changing the format.
4. **The snapshot on connect is a requirement, not decoration.** `STREAM_MAX_LIFETIME_SEC`
   forces the panel to switch connections every 15 minutes, and a blocked session emits **no**
   event at all in that time (measured: 98% of blocks). Without this, a block lasting 40
   minutes would vanish from the screen after fifteen.

A single entry that cannot be accepted is **skipped**, and the rest of the set goes through:
clearing an alert because of a formatting error would be the same kind of mistake as a false
zero in a measurement. An entry older than 24 h drops out of the snapshot — a machine that
disappeared mid-block will never send a correction.

**`contractVersion` does not go up.** Adding a frame is a non-breaking change — exactly like
adding the whole of `/api/stream` and the `deltaFrom` field (§ 11). The browser does not see
it by construction: `useLiveStream.ts` registers five named listeners and **has no
`onmessage`**.

This path **must** be covered by the `X-Ingest-Key` edge filter in Apache, exactly like
`/ingest` — without it, it is wide open to scanners and defends itself with the Bearer alone.

---

## 4. Four freshness states — a data contract, not four drawings

Every series in `/status` has a `freshness` field. **The backend distinguishes four states and
that has not changed** — it is still the contract. What changed is something else: **the UI
does not name them and does not draw them separately.** Currency is carried entirely by the
**reading-age label**.

| `freshness` | Meaning | `utilization` | How to show it |
|---|---|---|---|
| `live` | **Confirmation** fresher than 5 min (since v3 — not a sample, see below) | a number | full track + number + reading age |
| `stale` | No confirmation for 5 min, but the window is still running | a number | **identical to `live`** |
| `unknown` | The client is reporting, but there is no data for this series | **`null`** | **identical to `live`**, the value from `rawUtilization` |
| `inferred_reset` | The window reset and the client stayed silent — we **infer** ~0% | `0.0` | clearly different from a measurement: `~0`, an outline with no fill |

The first three rows have **one drawing and one wording**, because all three carry the
**last true value**. What tells them apart is only how old the reading is — and that is what
the caption says:

```
confirmed at 11:58 · 5 min ago
confirmed on Sun. at 11:58 · 3 d 2 h ago
```

**Why not four drawings.** `stale` used to get a dimmed fill plus "the value may already be
higher", and `unknown` an outline with hatching and the word "unknown" **instead of a
number**. Both were noise over information the reading age states directly and more precisely:
`stale` only meant "5 minutes have passed", and `unknown` **hid a known, true percentage**.
The AX206 panel was built on the age-based model from the start (`panel/panel/view.py`), and
that is the pattern here — the web caught up to it, not the other way around.

### The most important sentence in this document

**`unknown` must never be rendered as 0%.**

This sentence stays, and after the change it holds **more strongly**. This tool's worst
failure mode is showing a false, confident-looking zero — because on that basis a user kicks
off a big task and hits a wall. That is exactly why the backend returns `utilization: null` in
this state, with `rawUtilization` beside it carrying the **last measured** value.

The rule for the UI is therefore: **`utilization ?? rawUtilization`**, never
`utilization ?? 0`. We show the last **measurement** with its age, never zero and never an
invented number. The word "unknown" and the dashed track stay **only** for a series that has
**never** been measured (`utilization` and `rawUtilization` both `null`) — there an empty
track would read as zero, and zero is not allowed.

The same sentence is the reason for how `unavailableReason` is handled, and **in both
directions**. The phantom is `percent: 0` **from the withdrawal payload** — not a value measured earlier. That is why
with a withdrawn meter `utilization` is `null` (there is no current figure), but
`rawUtilization` **stays**: it is the last MEASURED percent and the only thing known about
usage. Deleting it would be the same mistake as rendering zero, just from the other side — the
user loses a figure we genuinely have.

The whole row then describes **that measurement**: `rawUtilization`, the amounts in `extra`
(`used`/`limit` from before the block, since the withdrawal payload zeroes them) and
`capturedAt`/`confirmedAt`/`valueSince` pointing at the moment this number was last confirmed.
The row looks exactly like a normal one — the UI shows
"confirmed on Wed. at 13:39 · 2 d 5 h ago", not "confirmed just now".

The dashed track and the words "no meter" stay only when there has **never** been a
measurement — there is still nothing to draw there.

The opposite mistake is just as dangerous and a separate test guards it: **exhausting your own
pool is not withdrawing the meter**. There `enabled` stays `true`, `unavailableReason` is
`null`, and the meter shows 100% (measured: 300.04 of 300.00 EUR) — and that number has to
stay on screen, because it is the only true one.

`inferred_reset` is also an inference, not a measurement, and it **still has to look
different** — a tilde next to the number and an outline with no fill. We deliberately do not
put the last measurement's stamp there: that one belongs to the **previous** window, and `~0`
speaks of the current one. A caveat for the tooltip: the inference is true **unless** the
account was used in that time from claude.ai, mobile, or Cowork — those draw on the same limit
but send no samples.

### `warnings[]` no longer carries anything

The backend used to generate one warning: "some series on account X are in the `unknown`
state". **It disappeared along with the concept of `unknown` in the UI** — it repeated, by
state name, what was already shown next to it as a number of minutes. The field stays in
the contract as a place for facts that span accounts; **an empty `warnings[]` is a valid
state**, not a missing implementation.

### Sample freshness ≠ number freshness

`capturedAt` is the moment **we** observed the value, not the moment the value became true.
The caption never speaks in those terms: it says "confirmed", and it is computed from
`confirmedAt`.

This moment is computed by the **server**: `min(client_time + offset, receipt_time)`, where
`offset = receipt_time − measurement.sent_at`. So the client supplies the *age* of the
measurement, not a date — its wall clock has no effect on the timeline. The practical effect
for the UI: `spend:org` and `extra:usage` more often show an age on the order of minutes,
because they come from the Claude Code cache and not from a fresh `/usage` dump. **This is the
truth surfacing, not a regression** — they used to get the dump's time, which the data did not
actually carry.

**Freshness is computed from `confirmedAt`, not from `capturedAt`** — this is the v3 change.
Under v2 a stable value fell into `stale` purely because of dedup, so "nothing is changing"
looked identical to "we lost the connection".

---

## 5. Three hard rules of presentation

They follow from the data, not from taste.

**1. The plan is visible next to every account.** `orgType`, `seatTier`, `rateLimitTier`,
`email`. Without this, "40%" twice looks the same and means **different absolute amounts** —
different on a Max 20x, different on a Team Standard seat.

**2. Never sum or average percentages across accounts.** It is a number with no meaning.

**3. Comparisons over time are faceted per account by default**, not overlaid on one axis.
Overlay only deliberately, and then the legend carries the plan.

---

## 6. Rules about the data

**Zero hardcoded bucket names.** Anthropic's response has 17 top-level keys, of which 5 were
not known from any source (`amber_ladder`, `iguana_necktie`, `nimbus_quill`, `tangelo`,
`omelette_promotional`). Render whatever `/status` and `/series` return, sorted by
`sortOrder`. A new bucket has to appear **with no change to the UI code**.

**`primary` and `duplicateOf`.** The API reports the same limit twice — once as a top-level
bucket, once as an entry in `limits[]`. The backend pairs them from the data and marks the
duplicates. **Show only `primary: true`.** The entry from `limits[]` wins, because it carries
`isActive` and `severity`. When the values diverge, no pair forms and both series stay
visible — that is deliberate, we would rather show the divergence than hide it.

**A second pair is marked by source, not by value: `spend` and `extra_usage`.** These are two
views of **the same pool** of credits, not two limits — so `extra:usage` gets `primary: false`
and `duplicateOf: "spend:org"`. Pairing by data would never catch them, because `spend.percent`
arrives rounded to a whole number (93) while `extra_usage.utilization` carries full precision
(92.656). `spend` wins, because it has amounts in a money type and `severity`; `extra:usage`
has neither `resetsAt`, nor `severity`, nor amounts, so as a row it has nothing to set it
apart.

**Turned off does not mean unneeded.** `extra:usage` stays in the response and is the **only**
place you see `spend_limit_reached`, `user_disabled` (you turned it off, not the
organization), `credits_ever_enabled` (never had credits ≠ turned off) and future
`daily`/`weekly` sub-limits. It also holds the only precise copy of the percentage. Our UI
folds this into the "?" explanation next to the spend row (`frontend/src/lib/credits.ts`) —
not into a second bar.

On an account that **never** had credits, `extra_usage.utilization` is `null` forever, so that
series never enters `series[]` at all and has no partner. `spend:org` then stays
`primary: true` on its own — do not infer anything about the state of credits from a series'
absence. Read the withdrawal reason from the **spend row's** `unavailableReason`, not from
`extra`: with a withdrawn meter `extra` describes the last true measurement, so it still holds
`disabled_reason: null` from when the gate was open.

**`isActive` says what is actually limiting you *right now*.** It is the single most valuable
field in the whole response and deserves to be exposed prominently. Observed: the binding
limit **jumps over time** — `weekly_all` in the morning, `session` after an intense session.

**`severity`** is a ready-made classification from Anthropic (`normal`, …). Use it instead of
inventing your own thresholds.

**Compute countdowns from the `serverNow`** in the response, not from the browser's clock.
`secondsToReset` is computed server-side.

**`resetsAt: null` has two different reasons and neither means "this series does not
reset".** Anthropic gives no boundary for a window at **0% usage** (visible in `limits[]`:
`weekly_scoped percent 0 → resets_at null`) — a 5-hour window before its first use simply has
no instance yet. Separately, the probe **zeroes a stale boundary from the cache** when the
window rolled over between the cache being written and being read (up to ~5 min). The caption
has to tell them apart:

| series state | caption |
|---|---|
| `resetsAt` today | `reset in 2 h 05 min · at 20:00` |
| `resetsAt` a few days out | `reset in 4 d 2 h · on Fri. at 20:00` — **the day is mandatory** |
| `resetsAt` in the past | `reset has passed · at 20:00` — **never** "reset in …" |
| `resetsAt: null`, `utilization: 0` | `window has not started` |
| `resetsAt: null`, `utilization > 0` | `reset time unknown` |
| a series with no window (`spend`, `extra_usage`) | `no reset` |

**The bare hour lies when the moment is far off.** A weekly window resets up to 7 days ahead,
and a reading is sometimes several days old — "at 20:00" then does not say which day. That is
why **every stamp read relative to "now"** goes through one function (`lib/time.ts`, `stamp` /
`atStamp`), which adds the day exactly when the moment is not from today:

| distance from "now" | stamp |
|---|---|
| today | `at 11:58` (with seconds in the hero, when < 1 h) |
| ±1 day | `yesterday at 23:50` / `tomorrow at 20:00` |
| ±2…6 days | `on Tue. at 11:58` |
| further | `26.07 at 11:58`, with the year when different: `26.07.2025 at 11:58` |

The preposition is **part of the stamp**, not text glued on around it — the format is what
decides it ("at 11:58", but "on Wed. at 11:58"), so the call site must not tack on its
own "at". The day difference is computed across **local midnights**, not by dividing
milliseconds: a day at a clock change has 23 or 25 h.

**The wire is in UTC, the screen is in the user's zone.** The conversion is done exclusively
by the presentation layer (`lib/time.ts`) — no value is converted before being sent or before
being compared. Wherever raw hours are visible with no "now" context (the history range), the
zone is **labelled** (`UTC+2`), because two zones on one screen with no label is the shortest
path to a misreading.

**Limits are a cascade, not a single number** — and since v2 the backend computes it, in
`cascade[]` on the account. Four rungs in order: `session` → `weekly` → `credits` →
`hard_block`, each with a `state` (`on` / `off` / `unknown`) and exactly one with
`isCurrent: true`.

`isCurrent` starts on the rung whose series has `isActive`, and **slides down when that rung
is exhausted** — an observed Team case: the weekly rung is `isActive` and at 100%, but work is
really running on credits. Showing the weekly rung as current would confuse "this is limiting
me" with "this is where it ran out".

**`state: "off"` and `state: "unknown"` are two different things.** "Credits are off" is
information, "I don't know whether you have credits" is the absence of it. Merging them would
show a way out of the limit that may not exist. When the slide-down ends on the `unknown`
rung, that one gets `isCurrent` — and the UI shows "unknown" there, it does not guess.

**Amounts in the cascade are in minor units with an exponent** — `usedMinor: 3820`,
`exponent: 2`, `currency: "USD"` means `38.20 USD`. The backend **does not format**; the UI
does. The same rule applies in `spend.extra`: `{"amount_minor": 0, "currency": "USD",
"exponent": 2}`.

**The same rule — a bare hour lies once the day is in doubt — governs ranges**: twenty hours
of client silence is the norm here, so a gap's
caption regularly crosses midnight and `21:57–17:49` reads like time travel. When the ends
fall on different days, both sides get a date: `26.07 21:57 – 27.07 17:49`.

**`gaps[]` in `/history` has two kinds**, and needs two different shadings:
- `client_silent` — there were no batches. **You were not working**, so there is nothing to
  measure.
- `no_samples` — batches came in, but for this series there was not one sample. **A failure**,
  the same one that shows up in `/status` as `unknown`.

A chart that paints both the same way lies in exactly the place this tool must not lie. With
this data source, **missing data is information**, not a chart bug.

**`resets_at` WOBBLES — never compare it for equality.** Measured: 49 samples in 3 h, one
session window, values from `00:59:59.014384` to `01:00:00.982268`. Anthropic adds its own
response's microseconds and a small second-level drift on top. A real reset shifts the
boundary by a **whole window** (5 h or 7 days), so the two are told apart with a tolerance,
not equality. The backend does this on the UI's behalf (`same_reset_window`), but if you
compute anything from `resetsAt` in the browser — keep this in mind.

---

## 7. A real `GET /status` response

Generated from the running system, a Max account, 2026-07-26 21:57 UTC. Trimmed to two series
(the full one has six) — both `primary`, since bucket duplicates are omitted here for
readability.

```json
{
  "contractVersion": 3,
  "serverNow": "2026-07-26T21:57:27.446632Z",
  "warnings": [],
  "accounts": [{
    "uuid": "00000000-0000-4000-8000-000000000003",
    "label": "you@example.org",
    "email": "you@example.org",
    "displayName": "Tomasz",
    "color": null,
    "orgType": "claude_max",
    "seatTier": null,
    "rateLimitTier": "default_claude_max_5x",
    "subscriptionType": "max",
    "isEnabled": true,
    "lastSampleAt": "2026-07-26T21:57:06.343335Z",
    "lastBatchAt": "2026-07-26T21:57:06.343335Z",
    "lastClientHost": "desktop",
    "cascade": [
      {"key": "session", "state": "on", "reason": null, "isCurrent": true, "utilization": 91.0,
       "seriesKey": "limit:session|session|-|-",
       "usedMinor": null, "limitMinor": null, "currency": null, "exponent": null},
      {"key": "weekly", "state": "on", "reason": null, "isCurrent": false, "utilization": 43.0,
       "seriesKey": "limit:weekly_all|weekly|-|-",
       "usedMinor": null, "limitMinor": null, "currency": null, "exponent": null},
      {"key": "credits", "state": "off", "reason": null, "isCurrent": false, "utilization": null,
       "seriesKey": "spend:org",
       "usedMinor": 0, "limitMinor": null, "currency": "USD", "exponent": 2},
      {"key": "hard_block", "state": "on", "reason": null, "isCurrent": false, "utilization": null,
       "seriesKey": null,
       "usedMinor": null, "limitMinor": null, "currency": null, "exponent": null}
    ],
    "series": [
      {
        "seriesId": 3,
        "seriesKey": "limit:session|session|-|-",
        "label": "Session",
        "source": "limit",
        "sortOrder": 15,
        "kind": "session",
        "group": "session",
        "bucketKey": null,
        "utilization": 91.0,
        "rawUtilization": 91.0,
        "unavailableReason": null,
        "resetsAt": "2026-07-27T00:59:59.056340Z",
        "secondsToReset": 10951,
        "capturedAt": "2026-07-26T21:57:05Z",
        "confirmedAt": "2026-07-26T21:57:05Z",
        "valueSince": "2026-07-26T21:41:07Z",
        "freshness": "live",
        "isActive": true,
        "severity": "critical",
        "deltaPct1h": 28.0,
        "deltaFrom": "2026-07-26T20:58:03Z",
        "primary": true,
        "duplicateOf": null,
        "extra": null
      },
      {
        "seriesId": 4,
        "seriesKey": "limit:weekly_all|weekly|-|-",
        "label": "Week (all models)",
        "source": "limit",
        "sortOrder": 25,
        "kind": "weekly_all",
        "group": "weekly",
        "bucketKey": null,
        "utilization": 43.0,
        "rawUtilization": 43.0,
        "unavailableReason": null,
        "resetsAt": "2026-08-01T15:59:59.056361Z",
        "secondsToReset": 496951,
        "capturedAt": "2026-07-26T21:57:05Z",
        "confirmedAt": "2026-07-26T21:57:05Z",
        "valueSince": "2026-07-26T21:10:44Z",
        "freshness": "live",
        "isActive": false,
        "severity": "normal",
        "deltaPct1h": 3.0,
        "deltaFrom": "2026-07-26T20:58:03Z",
        "primary": true,
        "duplicateOf": null,
        "extra": null
      }
    ]
  }]
}
```

Three things worth noticing in this one response:

- **`isActive` really does jump.** In the v1 example (19:07) it bound `weekly_all`; here,
  after an intense session, it binds `session` at 91% with `severity: "critical"`.
- **`cascade` says more than the top percentage** — `session` is the current rung, credits are
  `off`, so the hard block comes right after the weekly one.
- **`resetsAt` on both series ends in `.056340` and `.056361`** — a 21 µs difference, because
  that is the response's microseconds, not the window boundary. See the warning in § 6.

The `bucket:five_hour` and `bucket:seven_day` series in the same response have
`primary: false` and a `duplicateOf` pointing at the ones above — do not show them by default.

The `extra:usage` series is **not present at all** in this response, and that is correct: a
Max account never had credits, so `extra_usage.utilization` is `null` and the series never
passed the `everNonNull` filter. On a Team account it would appear here with `primary: false`
and `duplicateOf: "spend:org"`.

The `spend:org` series carries in `extra`:

```json
{"enabled": false, "cap": null, "limit": null, "balance": null,
 "used": {"amount_minor": 0, "currency": "USD", "exponent": 2},
 "can_purchase_credits": false, "can_toggle": false,
 "disclaimer": "Usage credits cover you when you hit your plan limits. …"}
```

## 8. A real `GET /history` response

24-hour range, the session series, a Max account. `points` trimmed to three.

```json
{
  "bucket": "5m",
  "points": [
    {"t": "2026-07-26T17:45:00Z", "min": 23.0, "max": 23.0, "avg": 23.0, "last": 23.0, "n": 1},
    {"t": "2026-07-26T17:50:00Z", "min": 25.0, "max": 25.0, "avg": 25.0, "last": 25.0, "n": 2},
    {"t": "2026-07-26T17:55:00Z", "min": 25.0, "max": 25.0, "avg": 25.0, "last": 25.0, "n": 1}
  ],
  "resets": ["2026-07-26T17:55:01Z", "2026-07-26T20:16:03Z"],
  "gaps": [
    {"from": "2026-07-25T21:57:27.476580Z", "to": "2026-07-26T17:49:02.415203Z",
     "kind": "client_silent"},
    {"from": "2026-07-26T19:17:20.357794Z", "to": "2026-07-26T20:16:03.677026Z",
     "kind": "client_silent"}
  ]
}
```

`bucket=auto` picks the aggregation to match the range's width: `raw` up to 6 h, `5m` up to
48 h, `1h` beyond that. Under aggregation, `min`/`max` exist so that **peaks survive**; the
deployed UI draws only `avg`, but the data for a min-max band is available.

Note the first gap: almost **20 hours of client silence** in a 24-hour range. That is not a
bug — data only accrues while you are working, and the chart is meant to show that plainly.

---

## 9. UI design decisions

A list of questions that had to be settled before the view existed — and the answers the
current interface rests on:

| Question | Decision |
|---|---|
| Number of screens | **Two: Live and History.** Accounts/Machines and Diagnostics stay with `curl` |
| Hierarchy of the Live view | **Session 5 h is always in the foreground.** `isActive` does NOT reorder the hierarchy — it is a thin line and the word `limiting` next to the series that binds you (`limiting now` in the hero) |
| Visualization | Horizontal track + number — **one drawing for the freshness states**, with the age of the reading carried by the caption beside it. Only `inferred_reset` and a never-measured series look different (§ 4) |
| Palette | Nocturne (structure) + **the warm Claude palette** (`--color-accent: #d97757` on `#1c1b19`) |
| Chart library | None — the chart is its own SVG, `viewBox 0 0 1000 200` |
| Widths | One layout in two: the full window (accounts as columns) and a narrow column |

**Why a fixed hero, not a moving one.** If the foreground jumped to follow `isActive`, the
same screen would mean something different depending on the time of the week. A fixed hero
plus a moving marker gives both: "how much is left in the window I'm working in" **and**
"what's really limiting me".

---

## 10. Without the UI, via `curl`

The UI covers Live and History; the rest of the data (events, batches, machines, raw
payloads) is available only this way:

```bash
# in a browser with a valid session — the simplest path
https://usage.example.org/claude-usage/api/status

# from a terminal; AUTH_MODE=verify needs a session cookie, `header` needs
# the header from the proxy, `none` needs nothing
curl -s -b "$COOKIE" https://usage.example.org/claude-usage/api/status | jq \
  '.accounts[] | {email, orgType,
    series: [.series[] | select(.primary) | {label, utilization, freshness, isActive}]}'

# what is limiting me right now
curl -s -b "$COOKIE" .../api/status | jq '.accounts[].series[] | select(.isActive)'

# the operational log — account switches, schema drift
curl -s -b "$COOKIE" .../api/events | jq '.[] | {ts, type, message}'
```

Locally, with no gate and no server: `python client/analyze-samples.py`

---

## 11. Contract versioning

`/status` returns `contractVersion` (currently **`3`**). On a breaking change the number goes
up — the UI checks this and **protests loudly** in the header, instead of silently rendering
garbage (`frontend/src/components/Nav.tsx`, the `CONTRACT_VERSION` constant in
`api/types.ts`).

**The same number rides in the envelope of every SSE frame** and means exactly the same thing,
because the `account` frame carries the same model. There are therefore two consumers:
`/status` and `/stream`. Adding the stream did **not** bump the version — `/status` did not
change by so much as a field.

Adding a field is a **non**-breaking change and needs no bump. The version went from 1 to 2
solely because of the change to time serialization — the rest of v2 is additions. `deltaFrom`
arrived the same way: the version stayed at 3, and a UI without this field gets `undefined`
and falls back to the hourly wording. The same goes for `label`: it is display text,
`seriesKey` is identity — changing a label does not bump `contractVersion` either.

The Team account has already been verified live, in three credit states at once: working
(93%, 277.95 of 300.00 EUR), an exhausted own pool (100%, 300.04 of 300.00 — `enabled` still
`true`), and a meter withdrawn by the organization (`enabled: false`, `disabled_reason`,
amounts zeroed). All three sit as fixtures in `backend/tests/fixtures/` and the cascade tests
rest on them — the invented payload with a 9000 USD threshold is gone.

One last pitfall: `cap` in the real response is **nested**
(`{"credits": null, "money": {"amount_minor": 30000, …}}`), not flat. A flat read passed tests
against the invented payload and did not work against the real one.
