#!/usr/bin/env python3
"""Claude limit probe + blocked-session signaller. Both run from Claude Code hooks.

TWO FUNCTIONS, ONE PROCESS — and that is measured, not aesthetic. The signaller used to
be a separate script (`client/session-status.py`), so 9 of its 10 events started a SECOND
CPython next to the one that was starting for the probe anyway. Measurement (100 runs per
variant, interleaved, median): folding the signaller code into this process costs 2.7 ms
(95% CI 1.9-3.2), a separate process 41.9 ms (41.7-42.3). Over a day, at ~21,000 events:
+57 s against +890 s. The old justification for the split ("+0.294 ms for every added
line") was overstated ~71-fold — really 0.0041 ms/line.

A side effect, also counted: 9 of the 13 shared names were byte-for-byte identical, and
`_extract_block` (~40 lines) differed only in a comment. The split FORCED the duplicate.

Does NOT call api.anthropic.com. Instead it asks Claude Code itself to measure
(`claude -p "/usage"`) and reads the result from the two places Claude Code leaves on disk.

SAFETY RULES — do not break them while extending:
  1. The probe makes NO request to api.anthropic.com. The only party calling
     /api/oauth/usage is Claude Code, over its own channel, with its own token, which
     it refreshes itself. That removes in one stroke: use of the OAuth token by a
     third-party tool (forbidden by the ToS), User-Agent impersonation and the risk of a ban.
  2. .credentials.json is opened READ-ONLY and ONLY for plan metadata.
     accessToken is neither taken from it nor used for anything.
  3. NEVER call the token endpoint (grant_type=refresh_token).
  4. The throttle is mandatory — PostToolUse fires on every tool.
  5. Zero heavy imports on the hot path. Stdlib only.
  6. Never raises an exception and never blocks the session.
  7. The probe NEVER waits for the child process. `claude -p "/usage"` takes ~3.4 s;
     the result is consumed ONLY by the next probe run.

TWO SOURCES, because freshness and completeness live in different places (measured):
  * stdout of `claude -p "/usage"` — FRESH on every call, but only the percentages
    of the main windows, as text. The values are whole numbers — and that is no loss,
    because the API itself returns whole numbers (verified on the raw payload).
  * ~/.claude.json -> cachedUsageUtilization.utilization — the FULL raw response
    body (spend, extra_usage, limits[], every bucket), but Claude Code rewrites it
    at most once every 5 minutes (a hard write throttle of its own).

We merge: the structure from the cache + fresh percentages from stdout laid on top.
The result has EXACTLY the same shape as the former HTTP response, so the backend parser
needs no changes.

i18n-keep: every diagnostic SLUG this file writes stays Polish — `brak-cache`,
`cache-przeterminowany`, `cache-innego-konta`, `nie-komenda-lokalna`, `stary-zrzut`,
`brak-procentow`, `zrzut-starszy-od-cache`, `reset-w-toku`, `okno-wygaslo`,
`brak-claude-w-path`, `brak-pliku-wyjscia`, `spawn-*`, `brak-credentials`, `odczyt-*`,
`rejestr-brzeg-sesji`, `rejestr-niepelny`, `rejestr-bez-biezacej-sesji`, and the JSON key
`"wpisy"`. They are not prose and nobody reads them as words: they are keys, tallied by a
generic Counter() in analyze-samples.py over usage-samples.jsonl, which is already
megabytes of history. Translating one does not rename a value, it SPLITS its count in
two — and the log outlives the code, so the split is permanent and silent.
`cache-z-przyszlosci` proves the point: it no longer exists anywhere in this file and is
still being counted from the log.

THE SIGNALLER (section "alert" below) detects the moment Claude Code has stopped and is
waiting for a HUMAN: a permission prompt, AskUserQuestion, ExitPlanMode. Each block is one
file in the state directory; the set of those files is the WHOLE truth, and the POST is
only a notification of change, carrying the set in full. It fires BEFORE the throttle —
an alert cannot wait 60 s, and the probe throttle is 60 s. "session_status": false turns it off.

Configuration: %LOCALAPPDATA%\\claude-usage-monitor\\config.json (Windows)
               ~/.local/state/claude-usage-monitor/config.json (Linux)
    {"ingest_url": "https://usage.example.org/claude-usage/api/ingest",
     "ingest_token": "<token of THIS machine>",
     "edge_key": "<shared edge secret>",
     "throttle_sec": 60,
     "claude_bin": "<optionally the full path to claude>",
     "session_status": true,        # signaller; false disables it entirely
     "alert_url": "https://usage.example.org/claude-usage/api/session-alert",
     "toast": true,
     "blocked_ttl_sec": 86400}
Deliberately a local file, not the repo — a machine token has no business in git.
"""
import sys, os, json, time, re

SCRIPT_VERSION = 12

# Marker inherited by the child process. `claude -p "/usage"` is a normal Claude Code
# session — it will fire the Stop hook, which fires the probe, which would fire another
# `claude`... The throttle alone will NOT stop this, because every child has its own clock.
CHILD_ENV = "CUM_PROBE_CHILD"

_base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/state")
OUTDIR = os.path.join(_base, "claude-usage-monitor")
CONFIG = os.path.join(OUTDIR, "config.json")
LOG = os.path.join(OUTDIR, "usage-samples.jsonl")
SPOOL = os.path.join(OUTDIR, "spool.jsonl")
THROTTLE_FILE = os.path.join(OUTDIR, "last-probe.txt")
CLI_OUT = os.path.join(OUTDIR, "usage-cli.json")

MAX_SPOOL_LINES = 5000
MAX_BACKLOG_PER_REQUEST = 200
CACHE_MAX_AGE_S = 3600          # the same as the read TTL on the Claude Code side
CLI_MAX_AGE_S = 900             # an older stdout dump is ignored — cache-only data is better
MAX_SANE_PCT = 101              # see the guards in parse_usage_text / sanitize


def _safe(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except Exception:
        return None


def log_local(rec):
    try:
        os.makedirs(OUTDIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_config():
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def throttled(seconds):
    """The marker is written BEFORE the call, so that parallel hooks do not stampede."""
    try:
        if time.time() - os.path.getmtime(THROTTLE_FILE) < seconds:
            return True
    except Exception:
        pass
    try:
        os.makedirs(OUTDIR, exist_ok=True)
        with open(THROTTLE_FILE, "w") as f:
            f.write(str(time.time()))
    except Exception:
        pass
    return False


# --------------------------------------------------------------- identity + cache
def _find(name, in_claude_dir=False):
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    cands = []
    if cfg:
        cands.append(os.path.join(cfg, name))
    home = os.path.expanduser("~")
    cands.append(os.path.join(home, ".claude", name) if in_claude_dir
                 else os.path.join(home, name))
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def _extract_block(text, key):
    """Extracts the balanced {...} block after a key. Immune to duplicate keys differing
    only in letter case (the same path spelled with the drive letter in both cases), on which
    whole-file parsers fail — ~/.claude.json REALLY does have such keys, json.load() over the
    whole file trips on them.

    Two callers: `read_claude_json` (account identity and measurement cache) and
    `account_uuid` from the alert section (which panel band the marker lands on). Before
    the two scripts were merged, this function existed in TWO copies."""
    i = text.find('"%s"' % key)
    if i < 0:
        return None
    start = text.find("{", i)
    if start < 0:
        return None
    depth, j, n, in_str, esc = 0, start, len(text), False, False
    while j < n:
        c = text[j]
        if in_str:
            if esc: esc = False
            elif c == "\\": esc = True
            elif c == '"': in_str = False
        else:
            if c == '"': in_str = True
            elif c == "{": depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:j + 1])
        j += 1
    return None


def _extract_scalar(text, key):
    """Extracts the SCALAR value after a key — string, number, bool or null.

    A sibling of `_extract_block`, which only handles `{...}`. `cachedExtraUsageDisabledReason`
    sits at the top level as a bare string or null, so that function cannot see it.

    `raw_decode` from the position past the colon instead of hunting for the end by hand:
    json itself knows where the value ends, and copes with quotes and escapes inside it.
    """
    i = text.find('"%s"' % key)
    if i < 0:
        return None
    j = text.find(":", i + len(key) + 2)
    if j < 0:
        return None
    j += 1
    while j < len(text) and text[j] in " \t\r\n":
        j += 1          # raw_decode does NOT tolerate whitespace before the value
    return json.JSONDecoder().raw_decode(text, j)[0]


_ACCT_FIELDS = ("accountUuid", "emailAddress", "organizationUuid", "organizationName",
                "organizationType", "organizationRateLimitTier", "userRateLimitTier",
                "seatTier", "hasExtraUsageEnabled", "displayName")


def read_claude_json():
    """One file read, three extracts: account identity, measurement cache and the reason
    credits are disabled.

    The former version cached the identity by mtime. This file now has to be read every
    time anyway (cachedUsageUtilization changes), so a separate cache was nothing but extra
    I/O. Switching accounts through /login rewrites this same file, so account-change
    detection still works — just without the middleman."""
    path = _find(".claude.json")
    if not path:
        return None, None, None, None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return None, None, None, None

    acct = None
    raw = _safe(_extract_block, text, "oauthAccount")
    if raw:
        acct = {k: raw.get(k) for k in _ACCT_FIELDS}

    cached = _safe(_extract_block, text, "cachedUsageUtilization")
    # The credits-disabled reason from the CLIENT cache. On its own it tells apart three
    # states that the in-band data does not: null / `org_spend_cap_reached` (the account's OWN pool
    # exhausted, where `spend.disabled_reason` is null) / `org_level_disabled_until` (the
    # organization ceiling). Collected FOR INSPECTION — the verdict still rests on in-band data.
    reason = _safe(_extract_scalar, text, "cachedExtraUsageDisabledReason")
    return acct, cached, os.path.dirname(path), reason


# --------------------------------------------------------------- plan metadata
def load_token_meta():
    """Plan metadata ONLY. accessToken is deliberately not returned — since version 3 the
    probe authenticates nothing. A missing file (macOS keeps credentials in the Keychain)
    is no longer a fatal error: measurement keeps working, only the plan tags disappear."""
    path = _find(".credentials.json", in_claude_dir=True)
    if not path:
        return {"reason": "brak-credentials"}
    try:
        with open(path, "r", encoding="utf-8") as f:      # READ-ONLY
            oa = (json.load(f).get("claudeAiOauth") or {})
    except Exception as e:
        return {"reason": "odczyt-%s" % type(e).__name__}
    exp = oa.get("expiresAt")
    return {"subscription_type": oa.get("subscriptionType"),
            "rate_limit_tier": oa.get("rateLimitTier"),
            "expires_in_s": int(exp / 1000.0 - time.time()) if exp else None}


# --------------------------------------------------------------- measurement via the CLI
def find_claude(cfg):
    b = cfg.get("claude_bin")
    if b and os.path.isfile(b):
        return b
    import shutil                       # local import — cold path, once every 60 s
    return shutil.which("claude")


def spawn_refresh(cfg):
    """Fires `claude -p "/usage"` and returns IMMEDIATELY. The next run reads the result.

    /usage is registered twice; the variant with supportsNonInteractive is the one active
    in -p mode. It returns {type:"text"}, which sets shouldQuery=false — meaning no model
    turn happens at all. Measured: num_turns=0, duration_api_ms=0, total_cost_usd=0.
    Measuring the limit does not consume the limit.

    --no-session-persistence turns off transcript writing. Without it every call leaves
    a ~4 KB file in ~/.claude/projects/<cwd> — measured 102 files over 2h35m of work. The
    flag works only with -p. Verified A/B: with the flag 0 files, without it 1 file, while
    the cachedUsageUtilization cache still refreshes (and the merge depends on it).

    --model haiku is a safety net, idle on the happy path: /usage returns
    shouldQuery=false, so no model is invoked at all (measured: num_turns=0, cost 0, time
    unchanged). It matters only when the argument misses the local command — then a paid
    turn runs, which without this flag would go to the model from settings.json.
    The probe will reject such a dump anyway on num_turns>0, but the cost has already been
    paid; the flag cuts it by an order of magnitude. An alias, not a dated ID — IDs get
    withdrawn.

    --strict-mcp-config --mcp-config {"mcpServers":{}} cuts off the MCP boot — the probe
    does not use it, and it is a dozen or so node/npx processes per run."""
    exe = find_claude(cfg)
    if not exe:
        return "brak-claude-w-path"
    import subprocess
    env = dict(os.environ)
    env[CHILD_ENV] = "1"                # barrier against hook recursion
    kw = {}
    if os.name == "nt":
        # CREATE_NO_WINDOW (hidden console, inherited by grandchildren) | NEW_PROCESS_GROUP
        # (no Ctrl+C from the parent). Do NOT add DETACHED_PROCESS (0x8) — it overrides
        # CREATE_NO_WINDOW, and grandchildren then allocate their own, visible console.
        kw["creationflags"] = 0x08000000 | 0x00000200
    else:
        kw["start_new_session"] = True
    try:
        os.makedirs(OUTDIR, exist_ok=True)
        out = open(CLI_OUT, "wb")
    except Exception:
        return "brak-pliku-wyjscia"
    try:
        subprocess.Popen(
            [exe, "-p", "--no-session-persistence", "--model", "haiku",
             "/usage", "--output-format", "json",
             # last only: --mcp-config is variadic, it would eat the prompt as a path
             "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}'],
            stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.DEVNULL,
            cwd=OUTDIR,                 # neutral directory: no CLAUDE.md, no project hooks
            env=env, close_fds=True, **kw)
    except Exception as e:
        return "spawn-%s" % type(e).__name__
    finally:
        _safe(out.close)
    return None


# [^:\n] and not [^:] — a negated class matches the newline too, so without excluding
# \n the title chews through the header and the blank lines up to a colon on the NEXT line.
# The effect is insidious: short inputs parse fine, real output loses the first reading.
_PCT_RE = re.compile(r"^(?P<title>\S[^:\n]*):\s+(?P<pct>\d+)%\s+used", re.M)


def parse_usage_text(text):
    """Tolerant parser of /usage output. An unrecognized line is ignored, it is not an
    error. Titles are localizable and can change between versions — which is why the raw
    text goes into the payload anyway, alongside the parsed values.

        Current session: 47% used - resets Jul 27, 12:30pm (UTC)
        Current week (all models): 48% used - resets Aug 1, 6pm (UTC)
        Current week (Fable): 0% used

    The attribution section ("100% of your usage came from...") has no colon before the
    percentage, so it does not match the regex."""
    out = {"session": None, "weekly_all": None, "scoped": {}}
    for m in _PCT_RE.finditer(text or ""):
        title, pct = m.group("title").strip(), int(m.group("pct"))
        if pct > MAX_SANE_PCT:
            # Claude Code can leak the epoch from resets_at into the percent field (bug
            # #52326). We reject instead of clamping to 100: clamping turns an obvious
            # failure into a plausible-looking "limit maxed out", i.e. into a false alarm.
            continue
        low = title.lower()
        if low == "current session":
            out["session"] = pct
        elif low == "current week (all models)":
            out["weekly_all"] = pct
        elif low.startswith("current week (") and title.endswith(")"):
            out["scoped"][title[len("Current week ("):-1]] = pct
    return out


def read_fresh():
    """Reads the stdout dump left by the PREVIOUS run.

    The file is written by the child process, so a read can land mid-write — which is
    why validation goes through json.loads(): a truncated file simply does not parse and
    the cycle runs on the cache alone. No locking, no marker file."""
    try:
        age = time.time() - os.path.getmtime(CLI_OUT)
        with open(CLI_OUT, "r", encoding="utf-8", errors="replace") as f:
            d = json.loads(f.read())
    except Exception:
        return None, None, None
    if d.get("num_turns"):
        # num_turns>0 means "/usage" missed the local command and went to the model.
        # Such a result is worthless and expensive — we do not use it and we signal it.
        return None, None, "nie-komenda-lokalna"
    if age > CLI_MAX_AGE_S:
        return None, None, "stary-zrzut"
    parsed = parse_usage_text(d.get("result") or "")
    if parsed["session"] is None and parsed["weekly_all"] is None:
        return None, None, "brak-procentow"
    return parsed, os.path.getmtime(CLI_OUT), None


def dump_outdated(fresh_at, cache_at):
    """Whether the dump is OLDER than the cache — then there is nothing to lay on top.

    The whole merge assumes the dump is fresher. But a dump is allowed to be up to
    CLI_MAX_AGE_S (900 s) old, and ordinary work in Claude Code refreshes the cache in that
    time, so the order can invert (measured: 2 cases out of 1646 measurements, down to -105 s).

    The dangerous case is a WINDOW RESET between the dump and the cache. The percentage
    would then come from the dump, i.e. from before the reset (e.g. 95%), while `resets_at`
    comes ONLY from the cache, i.e. already from the new window. `sanitize` will not catch
    it — the boundary is valid, so nothing looks contradictory — and we publish 95% against
    a window that is really at ~1%. A confident-looking untruth, exactly at the moment the
    window is free. History additionally keeps a drop inside a single window, which
    `window_start_index` reads as a reset.

    In the normal direction that same reset is harmless: the dump gives ~1%, the boundary from
    the cache is expired, `sanitize` zeroes it and reports `reset-w-toku`.

    The cost of rejecting is ZERO: the cache value stays, and it is newer — and incidentally
    more accurate, because stdout truncates percentages to whole numbers."""
    return bool(fresh_at) and bool(cache_at) and fresh_at < cache_at


def _limit_model(lim):
    """The model's display_name from a limits[] entry — with TYPE GUARDS at every level.

    The shortcut `(lim.get("scope") or {}).get("model")` works only as long as `scope` is
    a dict or absent. This function runs for EVERY limit (because the coverage key contains
    the model regardless of `kind`), so `scope: "global"` would give an AttributeError in
    `merge()` — that is, before `log_local`, before the spool and before the POST. The run
    would vanish without a trace, on every following cycle, for as long as the cache has
    that shape. The backend carries the same guard (backend/app/parsing.py:386)."""
    scope = lim.get("scope")
    if not isinstance(scope, dict):
        return None
    model = scope.get("model")
    if not isinstance(model, dict):
        return None
    name = model.get("display_name")
    return name if isinstance(name, str) else None


def _limit_key(lim):
    """The coverage identifier for a limits[] entry. Must be IDENTICAL to what the backend
    computes (`parsing.probe_key`) — no slugging, the raw `display_name`. A divergence is
    SILENT: the set simply never matches and behavior falls back to the state before this
    change.

    `surface` does NOT go into the key, because `merge` matches on `kind`+`model` and does
    not tell surfaces apart — two limits differing only in it really are both covered."""
    return "limit:%s:%s" % (lim.get("kind") or "?", _limit_model(lim) or "-")


def merge(cached_usage, fresh):
    """The structure from the cache + fresh percentages on top.

    Returns a payload shaped identically to the former HTTP response, plus the list of
    series COVERED by the dump. Coverage is not the same as change: a fresh reading equal
    to the cached value IS a confirmation and has to count, because dating on the backend
    side (`measurement.fresh_covered`) and the `reset-w-toku` decision in `sanitize` both
    depend on this list."""
    if not fresh:
        return cached_usage, []
    usage = json.loads(json.dumps(cached_usage))      # a copy — the source is not mutated
    covered = []

    def put(bucket, val):
        b = usage.get(bucket)
        if not isinstance(b, dict) or val is None:
            return
        b["utilization"] = val
        covered.append("bucket:%s" % bucket)

    put("five_hour", fresh["session"])
    put("seven_day", fresh["weekly_all"])

    for lim in (usage.get("limits") or []):
        if not isinstance(lim, dict):
            continue
        kind, val = lim.get("kind"), None
        if kind == "session":
            val = fresh["session"]
        elif kind == "weekly_all":
            val = fresh["weekly_all"]
        elif kind == "weekly_scoped":
            val = fresh["scoped"].get(_limit_model(lim))
        if val is None:
            continue
        lim["percent"] = val
        covered.append(_limit_key(lim))
    return usage, covered


def _epoch(iso):
    """resets_at arrives as ISO-8601 with an offset: 2026-07-27T10:29:59.761469+00:00."""
    if not iso:
        return None
    try:
        from datetime import datetime          # a C module, the import is negligible
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return None


def sanitize(usage, covered, now):
    """Rejects data from a window that has already reset, and absurd percentages.

    The heart of the problem: `resets_at` comes ONLY from the cache, which is up to 5
    minutes old (and up to an hour in fallback mode). If the window reset in the meantime,
    the pair (percent, resets_at) is internally contradictory. Two cases, two reactions:

    The question is "did the series get a FRESH percent", so we read `covered`, not the
    list of changes. Earlier the list of changed values went here, so a series with a fresh
    percent EQUAL to the cached one and an expired window was thrown out of the measurement
    instead of getting `reset-w-toku` — meaning the one true reading was lost.

      * the series got a fresh percent  -> the percent is true, only the reset time is
        stale. We zero resets_at; the next cache write (<=5 min) supplies a new one.
      * the series got NO fresh value   -> the percent comes from the expired window too.
        Publishing a former 95% as current would be a serious error (really it is ~0%), so
        the whole series is thrown out of this cycle.

    Returns a list of events for diagnostics — silence while discarding data is worse than
    missing data, because it looks like a correct measurement."""
    events = []
    covered_set = set(covered)

    for key, bucket in list(usage.items()):
        if not isinstance(bucket, dict) or "utilization" not in bucket:
            continue
        util = bucket.get("utilization")
        if isinstance(util, (int, float)) and util > MAX_SANE_PCT:
            usage[key] = None
            events.append("%s:absurd(%s)" % (key, util))
            continue
        exp = _epoch(bucket.get("resets_at"))
        if exp and exp <= now:
            if ("bucket:%s" % key) in covered_set:
                bucket["resets_at"] = None
                events.append("%s:reset-w-toku" % key)
            else:
                usage[key] = None
                events.append("%s:okno-wygaslo" % key)

    kept = []
    for lim in (usage.get("limits") or []):
        if not isinstance(lim, dict):
            continue
        pct = lim.get("percent")
        kind = lim.get("kind") or "?"
        if isinstance(pct, (int, float)) and pct > MAX_SANE_PCT:
            events.append("limit:%s:absurd(%s)" % (kind, pct))
            continue
        exp = _epoch(lim.get("resets_at"))
        if exp and exp <= now:
            if _limit_key(lim) in covered_set:
                lim["resets_at"] = None
                events.append("limit:%s:reset-w-toku" % kind)
            else:
                events.append("limit:%s:okno-wygaslo" % kind)
                continue
        kept.append(lim)
    if "limits" in usage:
        usage["limits"] = kept
    return usage, events


# --------------------------------------------------------------- spool
def read_spool(limit):
    try:
        with open(SPOOL, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
    except Exception:
        return [], 0
    out = []
    for l in lines[:limit]:
        rec = _safe(json.loads, l)
        if rec is not None:
            out.append(rec)
    return out, len(lines)


def append_spool(rec):
    try:
        os.makedirs(OUTDIR, exist_ok=True)
        with open(SPOOL, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with open(SPOOL, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        if len(lines) > MAX_SPOOL_LINES:
            with open(SPOOL, "w", encoding="utf-8") as f:   # the oldest are dropped
                f.write("\n".join(lines[-MAX_SPOOL_LINES:]) + "\n")
    except Exception:
        pass


def trim_spool(accepted):
    """Trimmed ONLY after confirmation of how many entries were accepted — a failure
    halfway through loses no data."""
    if accepted <= 0:
        return
    try:
        with open(SPOOL, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
        with open(SPOOL, "w", encoding="utf-8") as f:
            rest = lines[accepted:]
            f.write(("\n".join(rest) + "\n") if rest else "")
    except Exception:
        pass


_ssl_ctx = None


def ssl_context(cfg):
    """The Windows CA store used by Python rejects the Let's Encrypt chain of some hosts
    with 'certificate has expired', even though EVERY link is valid (verified with openssl:
    2026/2028/2032/2032). curl passes, Python does not — so the fault is the store's, not
    the server's. certifi works, so it is used whenever it is available.

    Verification is deliberately NOT disabled. `ca_bundle` in config.json allows pointing at
    a custom file, should certifi not be installed."""
    global _ssl_ctx
    if _ssl_ctx is not None:
        return _ssl_ctx
    import ssl
    cafile = cfg.get("ca_bundle")
    if not cafile:
        try:
            import certifi
            cafile = certifi.where()
        except Exception:
            cafile = None
    _ssl_ctx = ssl.create_default_context(cafile=cafile) if cafile \
        else ssl.create_default_context()
    return _ssl_ctx


def post(cfg, target, body):
    """One POST. `target` is explicit, because there are two callers: measurement ingest
    (`ingest_url`) and the signaller (`alert_url`). Both are authenticated by the same
    machine token and the same edge secret.

    Local imports — a cold path. `http.client` pulls in socket and ssl; those four modules
    at the top of the file cost ~23 ms on EVERY run, including the one that ends at the
    throttle. Do NOT move them up and do not add uses before this line.
    """
    import http.client, urllib.parse

    url = urllib.parse.urlsplit(target)
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if url.scheme == "https":
        conn = http.client.HTTPSConnection(url.netloc, timeout=5.0,
                                           context=ssl_context(cfg))
    else:
        conn = http.client.HTTPConnection(url.netloc, timeout=5.0)
    try:
        headers = {"Content-Type": "application/json",
                   "Authorization": "Bearer %s" % cfg.get("ingest_token", ""),
                   "Content-Length": str(len(data))}
        if cfg.get("edge_key"):
            headers["X-Ingest-Key"] = cfg["edge_key"]
        conn.request("POST", url.path or "/", body=data, headers=headers)
        r = conn.getresponse()
        return r.status, r.read().decode("utf-8", "replace")
    finally:
        _safe(conn.close)


def _iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(epoch)) + "Z"


# =============================================================== alert: the signaller
# Detects that Claude Code has stopped and is waiting for a human. This section touches
# NOTHING from the probe beyond the shared helpers (`_safe`, `load_config`, `_iso`,
# `_find`, `_extract_block`, `ssl_context`, `post`).
#
# WHAT WAS MEASURED, AND WHY THE CODE LOOKS EXACTLY LIKE THIS (Claude Code 2.1.221,
# VS Code, permission_mode=default, Windows; 224 events filtered by session_id):
#   * `PermissionRequest` fires ONLY on a real question to a human — auto-allowed
#     Read/Grep/Write/echo never generate it. Zero heuristics.
#   * `PermissionRequest` HAS NO `tool_use_id`. Hence the hybrid key.
#   * `tool_input` for AskUserQuestion CHANGES between entry and exit (the harness merges
#     the answers in, 1326 -> 1649 B), so a hash of tool_input cannot be the only key.
#     Those two tools are keyed by the `tool_use_id` from PreToolUse.
#   * NOTHING that ends a call other than by normal execution generates an event:
#     denial by button, Esc on the prompt and Esc while running — 5/5 of the cases end
#     at PreToolUse + PermissionRequest and nothing more. `PermissionDenied` never fired,
#     `is_interrupt: true` turned out to be unreachable. That is why sweeping by the
#     session_id prefix is MANDATORY, not precautionary.
#   * ...but in the TRANSCRIPT such an ending leaves a `tool_result` — measured on
#     2.1.223 three times, with three different contents (denial by button, Esc on the
#     prompt, closing the window with a question left hanging). Hence the second way out:
#     `closed_by_transcript`, the only one that clears the alert of a session that FELL
#     SILENT after a denial. Details at that function.
#   * `Stop` does NOT fire on an interrupted turn, and a denial ends the turn precisely
#     as an interrupt. That is why `UserPromptSubmit` comes first in the sweep list.
#   * `PostToolUse` is not guaranteed (Edit on a plan file: 0/6 closed, on other files
#     4/4). `PostToolBatch.tool_calls[]` closes those.

STATEDIR = os.path.join(OUTDIR, "session-status")
POSTED = os.path.join(OUTDIR, "session-status-posted.txt")

DEFAULT_TTL_S = 86400
DETAIL_MAX = 120
MAX_ENTRIES = 64            # a ceiling in case sweeping fails; the panel shows a few anyway

# These two tools ALWAYS block, so PreToolUse gives no false positives on them —
# and it carries the `tool_use_id` that `PermissionRequest` lacks.
ENTER_TOOLS = {"AskUserQuestion": "question", "ExitPlanMode": "plan"}

CLOSING_EVENTS = ("PostToolUse", "PostToolUseFailure")
SWEEP_EVENTS = ("UserPromptSubmit", "Stop", "SessionEnd")

# Fields needed LOCALLY ONLY, for closing from the transcript. `snapshot()` strips them:
# `transcript_path` carries the user's home-directory path, and `prompt_id` has no
# recipient in `SessionAlert`.
LOCAL_FIELDS = ("transcript_path", "prompt_id", "registry_seen")

# The transcript tail. Measured on sessions that fell silent after being resolved: the
# distance from the resolution to EOF is at most 366 B (n=8), and allowing <=4 records
# after it, at most 12.9 KB (n=14). 32 KB is 2.5x above that maximum. Past the threshold
# the mechanism finds NOTHING and the entry falls back to TTL — the failure direction is safe.
TAIL_BYTES = 32768

_ACCT_CACHE = {}


def account_uuid():
    """`oauthAccount.accountUuid` from ~/.claude.json, cached on mtime.

    Read ONLY on the entry path (a rare one), so it does not replace `read_claude_json` —
    that one reads the same file after the throttle and for much more than the uuid. The panel
    puts the marker on one specific account band, so it has to know which band the alert
    belongs to; AGENTS.md rule 7 says identity comes from here and from here only.
    """
    path = _find(".claude.json")
    if not path:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    hit = _ACCT_CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return None
    block = _safe(_extract_block, text, "oauthAccount") or {}
    uuid = block.get("accountUuid")
    uuid = uuid if isinstance(uuid, str) else None
    _ACCT_CACHE[path] = (mtime, uuid)
    return uuid


# --------------------------------------------------------------- project name
def _slug(path):
    """Path -> transcript directory slug. Every character outside [A-Za-z0-9] becomes '-'.

    Normalization on both sides, so a possible difference in how Claude Code treats the
    underscore cannot make us diverge: the same filter is applied to the directory name."""
    return "".join(c if (c.isascii() and c.isalnum()) else "-"
                   for c in os.path.normcase(path))


def project_name(cwd, transcript_path):
    """The project name — from the transcript directory, NOT from `basename(cwd)`.

    Measured: 38 of 73 sessions report more than one `cwd` (within a single session,
    simultaneously ...\\claude-usage-monitor, ...\\backend, ...\\frontend, ...\\frontend\\src
    — the header would show "src"), and 27 of 51 distinct `cwd` values are
    `...\\.claude\\worktrees\\agent-a<hex>`.

    "Walk up to .git" is wrong too: a worktree root has `.git` as a FILE, so the walk-up
    stops at the worktree and returns `agent-a00ce9ba287d12ab1`.

    Transcripts live under ~/.claude/projects/<slug of the session's ORIGINAL cwd>/, and
    that directory was measured to contain project roots ONLY. The root is recovered by
    trimming `cwd` segments until the slug of the prefix matches the directory name. Zero
    I/O on the main path.
    """
    if not isinstance(cwd, str) or not cwd:
        return None
    want = None
    if isinstance(transcript_path, str) and transcript_path:
        want = _slug(os.path.basename(os.path.dirname(transcript_path)))
    path = os.path.normpath(cwd)
    if want:
        probe = path
        while True:
            if _slug(probe) == want:
                return os.path.basename(probe) or probe
            parent = os.path.dirname(probe)
            if parent == probe:
                break
            probe = parent
    # Fallback: a directory with `.git` as a DIRECTORY (a file = worktree, not wanted here).
    probe = path
    while True:
        if _safe(os.path.isdir, os.path.join(probe, ".git")):
            return os.path.basename(probe) or probe
        parent = os.path.dirname(probe)
        if parent == probe:
            return os.path.basename(path) or path
        probe = parent


# --------------------------------------------------------------- entry key
def call_key(tool_name, tool_input, prompt_id):
    """sha256(prompt_id | tool_name | json(tool_input)) [:16].

    Used where `tool_use_id` does not exist — that is, for `PermissionRequest`.
    Measured as stable for Bash/Edit/Read/Grep/Write: 36 calls, zero collisions, exactly
    one tool_use_id per key. Known degenerate case: two calls IDENTICAL character for
    character within one prompt_id share a key, so the exit of the first removes the entry
    of the second. Rare and cheap.
    """
    import hashlib
    blob = "%s|%s|%s" % (prompt_id or "", tool_name or "",
                         json.dumps(tool_input, sort_keys=True, ensure_ascii=False,
                                    default=str))
    return hashlib.sha256(blob.encode("utf-8", "replace")).hexdigest()[:16]


def _fname(session_id, agent_id, key):
    return "%s__%s__%s.json" % (session_id, agent_id or "main", key)


def key_of(name):
    """The key from an entry's file name, or None for a name not of this form.

    A name outside the scheme really can be there (a test drops `garbage.json`), and then it
    must neither be deleted nor guessed at.
    """
    if not name.endswith(".json"):
        return None
    parts = name[:-5].split("__")
    return parts[2] if len(parts) == 3 else None


# --------------------------------------------------------------- state directory
def entries():
    """All entries: [(file_name, mtime)]. `scandir`, NEVER `os.stat(path)`.

    Measured against a reader in a loop: `os.stat(path)` fails in 35% of cases under
    maximum load, `scandir` + `DirEntry.stat()` in 0% — the latter is served from the
    directory enumeration record and opens nothing.
    """
    out = []
    try:
        with os.scandir(STATEDIR) as it:
            for de in it:
                if not de.name.endswith(".json"):
                    continue
                try:
                    out.append((de.name, de.stat().st_mtime))
                except OSError:
                    continue
    except OSError:
        return []
    return out


def read_entry(name):
    try:
        with open(os.path.join(STATEDIR, name), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def write_excl(name, payload):
    """A write through O_CREAT|O_EXCL. Returns True when the file was CREATED just now.

    Measured: 0 hard failures over 10,978 attempts against a reader in a loop.
    The "temp + os.replace" variant is out, because CPython opens without
    FILE_SHARE_DELETE, so a reader's handle blocks `replace` and `remove` (reproduced:
    WinError 5 / 32). O_EXCL incidentally preserves `since` for free and WITHOUT a read:
    FileExistsError means "this block already exists", so the stamp of the first entry
    stays untouched.
    """
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_BINARY", 0)
    try:
        os.makedirs(STATEDIR, exist_ok=True)
        fd = os.open(os.path.join(STATEDIR, name), flags, 0o600)
    except FileExistsError:
        return False
    except Exception:
        return False
    try:
        # ensure_ascii=False + explicit utf-8, as in log_local: without it cp1250 blows up
        # on a path with Polish characters in `detail`.
        os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    except Exception:
        pass
    finally:
        _safe(os.close, fd)
    return True


def drop(name):
    """Idempotent deletion. The only fragile operation in this section — a reader's handle
    can block it, hence three attempts. A failure is a stuck alert, not a lost one: every
    following exit event tries again."""
    path = os.path.join(STATEDIR, name)
    for _ in range(3):
        try:
            os.remove(path)
            return True
        except FileNotFoundError:
            return False
        except OSError:
            time.sleep(0.01)
    return False


def sweep_ttl(ttl_s, now):
    """The expiry cutoff. It deletes, it NEVER hides — when an alert TAKES OVER the
    screen is decided by the panel (`alert_takeover_sec`), not by this threshold. But
    there the takeover window belongs to the set, so an old entry comes back onto the card
    with every new block and only this threshold ends its life. The value comes from a manually
    edited config.json, so garbage means the default, not an exception.

    It does not publish itself and does not need to: `alert_dispatch` calls it right before
    `sweep_session`, which publishes unconditionally and compares against the marker there."""
    try:
        ttl_s = float(ttl_s)
    except (TypeError, ValueError):
        ttl_s = DEFAULT_TTL_S
    for name, mtime in entries():
        if now - mtime > ttl_s:
            drop(name)


# ----------------------------------------------------- harness session registry
# The harness keeps a registry of its sessions: `<pid>.json` with a `sessionId` field.
# Measured on 2.1.223, and this whole section rests on it:
#   * the record appears 0.2-1.0 s AFTER the session's first hook (13/13), so on
#     `SessionStart` its absence means nothing;
#   * on `SessionEnd` the record is still THERE (14/14) — it disappears 0.1-0.8 s later;
#   * closing a VS Code window cleans up the record in <=5 s, and the record left by a
#     KILLED process is removed by the harness itself on its first enumeration of the
#     registry (measured 626 ms after TerminateProcess); it fails to do so only on WSL and
#     when the last session on the machine was killed;
#   * the registry is NOT the set of all live sessions: `claude.exe` without a console does
#     not register at all (measured: 18 s of life, zero records). Hence `registry_seen`.
#
# FORBIDDEN: never `os.kill(pid, 0)` — on Windows it maps to `TerminateProcess`, i.e. the
# probe would kill Claude Code sessions, in code that by design never raises.
# Liveness is decided by the PRESENCE of a record and nothing else. The harness checks
# `procStart` itself, so pid recycling is not ours to watch either.


def registry_dir():
    """`$CLAUDE_CONFIG_DIR/sessions` or `~/.claude/sessions`.

    When the variable is set, `~` is NOT a fallback: a third-party config dir means
    `sessionId` values that belong to someone else, and those would be used to DELETE
    entries. Hence not `_find` — it returns files only and carries exactly that fallback.
    """
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg:
        return os.path.join(cfg, "sessions")
    return os.path.join(os.path.expanduser("~"), ".claude", "sessions")


REGDIR = registry_dir()         # at import time, without `stat` — as with STATEDIR


def live_sessions():
    """The set of `sessionId` values from the registry, or None when the set may be PARTIAL.

    None means "unknown" and NEVER means "empty" — that is AGENTS.md rule 4 carried onto this set.
    A directory read only halfway would shorten the live list and delete other sessions'
    entries wholesale, i.e. clear LIVE blocks. Hence one exception on any record invalidates the
    WHOLE run.

    A record is exclusively a `<digits>.json` file — the harness filters the same way (it
    parses the name with `parseInt` and rejects `NaN`). Everything else in this directory
    (`.in_use`, `.last_inuse_sweep` — a separate harness mechanism) is IGNORED, not counted
    as an unparsable record.
    """
    out = set()
    try:
        with os.scandir(REGDIR) as it:
            for de in it:
                if not de.name.endswith(".json") or not de.name[:-5].isdigit():
                    continue
                with open(de.path, "rb") as f:
                    rec = json.loads(f.read().decode("utf-8", "replace"))
                sid = rec.get("sessionId") if isinstance(rec, dict) else None
                if isinstance(sid, str) and sid:
                    out.add(sid)
    except Exception:
        return None
    return out


def registry_view(event, session_id):
    """(set of live sessions, reason for holding back). A `None` set = the death rule is NOT
    used."""
    if event in ("SessionStart", "SessionEnd"):
        # On these two the record is just being created or just disappearing, so its absence
        # proves nothing. The next `UserPromptSubmit` or `Stop` will do the collecting.
        return None, "rejestr-brzeg-sesji"
    live = live_sessions()
    if live is None:
        return None, "rejestr-niepelny"
    if session_id and session_id not in live:
        # The current session is ALIVE — this hook is running inside it. If it is not in the
        # registry, then we do not understand the registry (a different harness version, a
        # different directory, an unregistered session) and deletion of ANYTHING must not
        # rest on it.
        return None, "rejestr-bez-biezacej-sesji"
    return live, None


def entry_session(name):
    """The `session_id` from an entry's file name, or None for a name not of this form."""
    if not name.endswith(".json"):
        return None
    parts = name[:-5].split("__")
    return parts[0] if len(parts) == 3 else None


def registry_dead(name, live):
    """Whether this entry's session is no longer alive. A name outside the scheme is NOT
    dead — it belongs to someone else."""
    sid = entry_session(name)
    return bool(sid) and sid not in live


def registry_seen(session_id):
    """Whether THIS session is in the registry right now. Stored in the entry at creation.

    Only entries carrying this marker are subject to the death rule. Without it, an entry of
    a session the harness does not register would die immediately — and that is clearing a
    LIVE block, the one failure of this tool that costs real work. Read on the ENTRY path,
    i.e. the rare one.
    """
    live = live_sessions()
    return bool(live and session_id in live)


# --------------------------------------------------- closing from the transcript
# A denial and Esc generate NO hook event at all (measured 5/5), but they DO WRITE a
# `tool_result` into the transcript — measured three times, with three different contents:
# denial by button, Esc on the prompt ("The user doesn't want to proceed...") and closing
# the window with a question left hanging ("Tool permission request failed: AbortError...").
# That is why `is_error` is NOT a condition here, and the contents must not be matched by
# text: every `tool_result` means "resolved".
#
# This branch is the only mechanism that clears an alert after a denial in a session that
# THEN fell silent (`Stop` does not fire on an interrupted turn) — and it does so from the
# sweep of ANY session, because it walks the whole state directory, not its own prefix.


def _epoch(iso):
    """ISO-8601 UTC -> epoch (float) or None.

    On the PARSED time, never on a substring: `since` has one-second resolution while the
    transcript has milliseconds, so a lexicographic comparison inverts the result
    ("...:40.816Z" < "...:40Z", because '.' < 'Z') and would treat a later resolution as
    an earlier one.
    """
    if not isinstance(iso, str) or len(iso) < 19:
        return None
    import calendar                 # lazy, like `hashlib` in `call_key`
    try:
        base = calendar.timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return None
    frac = 0.0
    if len(iso) > 20 and iso[19] == ".":
        digits = ""
        for ch in iso[20:]:
            if not ch.isdigit():
                break
            digits += ch
        if digits:
            frac = _safe(float, "0." + digits) or 0.0
    return base + frac


def tail_records(path):
    """The last `TAIL_BYTES` bytes of the transcript as a list of parsed records.

    The handle is held for one read and no longer: CPython opens without FILE_SHARE_DELETE,
    so a long-held reader gets in the harness's way during its own operations on the
    transcript. The first line is incomplete ONLY when the read really started in the middle
    of the file.
    """
    size = os.path.getsize(path)
    off = max(0, size - TAIL_BYTES)
    with open(path, "rb") as f:
        f.seek(off)
        raw = f.read()
    lines = raw.decode("utf-8", "replace").splitlines()
    if off > 0 and lines:
        lines = lines[1:]
    out = []
    for line in lines:
        if not line.strip():
            continue
        rec = _safe(json.loads, line)      # a broken line must not kill the rest of the tail
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _blocks(rec, rec_type, block_type):
    """`block_type` blocks from a record of type `rec_type`. STRUCTURAL matching.

    Measured: `tool_use` occurs exclusively in `assistant` records (43,597/43,597), and
    `tool_result` exclusively in `user` ones (43,475/43,475). Substring matching is
    forbidden — the entry key always sits in the tail, in its own `tool_use` record, so a
    substring would clear every LIVE block on the first sweep.
    """
    if rec.get("type") != rec_type:
        return []
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return []
    content = msg.get("content")
    if not isinstance(content, list):
        return []
    return [b for b in content
            if isinstance(b, dict) and b.get("type") == block_type]


def result_record(records, tool_use_id):
    """The `user` record with a `tool_result` for this `tool_use_id`, or None."""
    found = None
    for rec in records:
        for b in _blocks(rec, "user", "tool_result"):
            if b.get("tool_use_id") == tool_use_id:
                found = rec
    return found


def _resolved_ids(records, prompt_id, since):
    """The `tool_use_id` values of resolutions that may concern THIS block. Two of the three
    conditions.

    Condition 1: `promptId` must be from this turn. That field is ONLY on `tool_result`
    (7695/7695), `tool_use` does not have it — hence this function's whole roundabout route.
    Condition 2: the resolution must be LATER than entry into the block. `prompt_id` spans
    an entire user turn (measured 5-12 calls, 89-199 s), so an identical retry within the
    same turn is real and condition 1 does not catch it.

    This filter goes FIRST, before any hash, and that is a decision about cost, not about
    style: computing `call_key` over ALL `tool_use` entries in the tail was measured at
    a 27.8 ms median with 64 entries (max 495 ms), while resolutions from this turn are
    a handful in the tail.
    """
    out = set()
    if not prompt_id or since is None:
        return out
    for rec in records:
        if rec.get("promptId") != prompt_id:
            continue
        when = _epoch(rec.get("timestamp"))
        if when is None or when <= since:
            continue
        for b in _blocks(rec, "user", "tool_result"):
            out.add(b.get("tool_use_id"))
    return out


def transcript_closed(data, key, records):
    """Whether the entry is resolved according to the transcript tail."""
    if not records:
        return False
    if data.get("reason") in ("question", "plan"):
        # The entry key IS the `tool_use_id` — there is nothing to recover.
        return result_record(records, key) is not None

    prompt_id = data.get("prompt_id")
    if not prompt_id:
        return False                # older probe: a hash over an empty string cannot tell
                                    # turns apart
    resolved = _resolved_ids(records, prompt_id, _epoch(data.get("since")))
    if not resolved:
        return False

    # A `permission` entry key is `call_key(tool_name, tool_input, prompt_id)`, i.e. a hash
    # that the transcript does not contain. Its ingredients are there, so it is recomputed
    # with THE SAME function — not a rewritten formula, because `json.dumps` there carries
    # `ensure_ascii=False, default=str`, and paths with Polish characters are real. The
    # ingredients were measured to be identical: `tool_input` from the hook reproduces
    # `input` from the transcript byte for byte 202/202 (Bash, Edit, Write, PowerShell, Read
    # and more). The pre-filter on the tool name is FREE and does not change the result:
    # `call_key` is computed from `tool_name`, so a different name cannot yield that key.
    # A real tail is mixed (Read, Edit, Bash...), so this sifts out most candidates before
    # the hash.
    tool = data.get("tool")
    tool_uses = [b for rec in records for b in _blocks(rec, "assistant", "tool_use")]
    match = None
    for i, b in enumerate(tool_uses):
        if (b.get("id") in resolved and b.get("name") == tool
                and call_key(b.get("name"), b.get("input"), prompt_id) == key):
            match = (i, b)              # with several hits the LAST one counts
    if match is None:
        return False

    # Condition 3: if a SECOND, byte-identical call appears in the tail after this one, then
    # that one is the live block and the entry must not be removed — both have the same
    # `call_key`, so the hash alone cannot tell them apart. Measured: 0.24% of calls have
    # such a twin within the 32 KB window, and the scenario "denial, Claude repeats an
    # identical call" occurred 22 times in the corpus.
    i, b = match
    for later_use in tool_uses[i + 1:]:
        if (later_use.get("name") == b.get("name")
                and later_use.get("input") == b.get("input")):
            return False
    return True


def transcript_path_of(data):
    """The entry's transcript file. For a subagent this is NOT the hook's `transcript_path`.

    Measured: a subagent's hook carries the PARENT's `transcript_path`, while the subagent's
    records live in a separate file. The path is computable and verified — every subagent
    `tool_use` was found there and nowhere else.
    """
    tp = data.get("transcript_path")
    if not isinstance(tp, str) or not tp:
        return None
    agent_id = data.get("agent_id")
    if not agent_id:
        return tp
    session_id = data.get("session_id")
    if not session_id:
        return None
    return os.path.join(os.path.dirname(tp), session_id, "subagents",
                        "agent-%s.jsonl" % agent_id)


def closed_by_transcript(name, data, tails):
    """One entry against its transcript. The tail is read ONCE per file, not once per entry."""
    path = transcript_path_of(data)
    if not path:
        return False                # an older probe, or a subagent without `session_id`
    if path not in tails:
        # `_safe` around the whole read of a single file: a locked file must not kill the
        # check of the remaining entries.
        tails[path] = _safe(tail_records, path) or []
    return transcript_closed(data, key_of(name), tails[path])


# --------------------------------------------------------------- sending the alert
def snapshot():
    """The machine's current set of entries, sorted by `since`. Local fields are stripped."""
    out = []
    for name, _mtime in sorted(entries()):
        data = read_entry(name)
        if data is None:
            continue
        for field in LOCAL_FIELDS:
            data.pop(field, None)
        data["key"] = name[:-5]
        out.append(data)
    out.sort(key=lambda e: e.get("since") or "")
    return out[:MAX_ENTRIES]


def _fingerprint(items):
    return json.dumps([[e.get("key"), e.get("reason")] for e in items], sort_keys=True)


def publish(cfg):
    """POST only when the SET has changed.

    The marker is written ONLY after a successful send, so a failed POST repeats on the next
    event. A known limitation, built into the design: a blocked session generates no further
    events, so a POST lost exactly at entry waits for the next activity in this or another
    session on the same machine.
    """
    items = snapshot()
    finger = _fingerprint(items)
    try:
        with open(POSTED, "r", encoding="utf-8") as f:
            if f.read() == finger:
                return
    except Exception:
        pass
    url = cfg.get("alert_url")
    if not url or not cfg.get("ingest_token"):
        return                      # local-only mode — files and toast, no network
    body = {"entries": items, "sent_at": _iso(time.time()),
            "script_version": SCRIPT_VERSION}
    try:
        code, _resp = post(cfg, url, body)
    except Exception:
        return
    if code >= 300:
        return
    try:
        os.makedirs(OUTDIR, exist_ok=True)
        with open(POSTED, "w", encoding="utf-8") as f:
            f.write(finger)
    except Exception:
        pass


# --------------------------------------------------------------- local toast
TOAST_TITLES = {"permission": "Claude is waiting for permission",
                "question": "Claude has a question for you",
                "plan": "Claude is waiting for plan approval"}


def _xml(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace("'", "&apos;").replace('"', "&quot;"))


def toast(reason, project, detail):
    """A Windows notification through WinRT, without any modules.

    BurntToast is not installed, and the `[xml]` variant from the guides does not work
    (`Cannot find type [Windows.Data.Xml.Dom.XmlDocument]`). What follows is verified:
    PowerShell 5.1, zero modules, 394 ms. The AUMID of a registered PowerShell is the
    carrier — Windows silently drops toasts from unregistered AppIDs.

    -EncodedCommand, NOT -Command: PowerShell 5.1 decodes the command line with the console
    code page and Polish characters came out as mojibake. Base64 from UTF-16LE is immune to
    that and incidentally removes the quoting problem in `detail`.
    """
    if os.name != "nt":
        return
    import base64, subprocess
    line1 = TOAST_TITLES.get(reason, "Claude is waiting for you")
    line2 = project or ""
    if detail:
        line2 = ("%s — %s" % (line2, detail))[:90] if line2 else detail[:90]
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications,"
        " ContentType=WindowsRuntime] > $null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
        " ContentType=WindowsRuntime] > $null;"
        "$x = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        "$x.LoadXml('<toast><visual><binding template=\"ToastText02\">"
        "<text id=\"1\">%s</text><text id=\"2\">%s</text>"
        "</binding></visual></toast>');"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("
        "'{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\\WindowsPowerShell\\v1.0\\powershell.exe'"
        ").Show((New-Object Windows.UI.Notifications.ToastNotification $x))"
    ) % (_xml(line1), _xml(line2))
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    try:
        subprocess.Popen(
            ["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            # CREATE_NO_WINDOW | NEW_PROCESS_GROUP — as in spawn_refresh. Do NOT add
            # DETACHED_PROCESS: it overrides CREATE_NO_WINDOW and the console flashes.
            creationflags=0x08000000 | 0x00000200, close_fds=True)
    except Exception:
        pass


# --------------------------------------------------------------- state machine
_DETAIL_KEYS = ("command", "file_path", "path", "url", "pattern", "description",
                "prompt", "plan")


def detail_of(tool_name, tool_input):
    if tool_name == "AskUserQuestion":
        qs = (tool_input or {}).get("questions")
        if isinstance(qs, list) and qs and isinstance(qs[0], dict):
            for k in ("question", "header"):
                v = qs[0].get(k)
                if isinstance(v, str) and v.strip():
                    return v.strip()[:DETAIL_MAX]
        return ""
    if not isinstance(tool_input, dict):
        return ""
    for k in _DETAIL_KEYS:
        v = tool_input.get(k)
        if isinstance(v, str) and v.strip():
            return " ".join(v.split())[:DETAIL_MAX]
    return ""


def enter(cfg, hook, reason, key):
    session_id = hook.get("session_id")
    if not session_id or not key:
        return
    name = _fname(session_id, hook.get("agent_id"), key)
    tool_name = hook.get("tool_name")
    entry = {
        "session_id": session_id,
        "agent_id": hook.get("agent_id"),
        "agent_type": hook.get("agent_type"),
        "reason": reason,
        "tool": tool_name,
        "detail": detail_of(tool_name, hook.get("tool_input")),
        "project": project_name(hook.get("cwd"), hook.get("transcript_path")),
        "cwd": hook.get("cwd"),
        "since": _iso(time.time()),
        "account_uuid": account_uuid(),
        # For diagnostics: the whole measurement ran in `default` mode, while auto-approving
        # modes resolve a call BEFORE the prompt layer.
        "permission_mode": hook.get("permission_mode"),
        # LOCAL fields (LOCAL_FIELDS) — `snapshot()` strips them before sending.
        # Measured that `PermissionRequest` carries both of the first two: 10/10, `prompt_id`
        # never `undefined`. `registry_seen` has to be created HERE: `write_excl` goes through
        # `O_EXCL`, so there is no way to add it afterwards.
        "transcript_path": hook.get("transcript_path"),
        "prompt_id": hook.get("prompt_id"),
        "registry_seen": registry_seen(session_id),
    }
    if not write_excl(name, entry):
        return                              # this block already exists — no toast, no POST
    if cfg.get("toast", True):
        toast(reason, entry["project"], entry["detail"])
    publish(cfg)


def _close_keys(hook, call):
    """Both key candidates for one call. The closing event does not have to know which
    mode created the entry — deletion is idempotent, so a miss costs nothing."""
    out = []
    tuid = call.get("tool_use_id")
    if tuid:
        out.append(tuid)
    out.append(call_key(call.get("tool_name"), call.get("tool_input"),
                        hook.get("prompt_id")))
    return out


def leave(cfg, hook):
    """A closing event. An event with an `agent_id` will never close an entry written
    without one — the agent segment is part of the file name, so the rule follows from the
    construction."""
    if not entries():
        return                              # the hot path ends here
    session_id = hook.get("session_id")
    if not session_id:
        return
    calls = [hook]
    if hook.get("hook_event_name") == "PostToolBatch":
        calls = [c for c in (hook.get("tool_calls") or []) if isinstance(c, dict)]
    hit = False
    for call in calls:
        for key in _close_keys(hook, call):
            if drop(_fname(session_id, hook.get("agent_id"), key)):
                hit = True
    if hit:
        publish(cfg)


def sweep_session(cfg, hook):
    """Sweeping by the `<session_id>__` prefix.

    MANDATORY, not precautionary: it is the only mechanism that clears an alert after a
    denial and after an interrupt, because neither of them generates an event of its own.
    `UserPromptSubmit` is the most important one in this list, because `Stop` does not fire
    on an interrupted turn.

    `SessionEnd` sweeps its OWN session_id only: it was measured to arrive ~once a minute
    with the identifier of the `claude -p` child spawned by this very script, so a global
    sweep would wipe alerts every minute.

    This loop also looks at entries of OTHER sessions, but it deletes them on one of two
    PROOFS, never on resemblance: either the session has no record in the harness registry
    (`registry_dead`), or its own transcript already carries a resolution
    (`closed_by_transcript`). Without this, an entry of a session that fell silent after a
    denial has no collector: that is the failure this came from.

    The rule based on matching `cwd` (`gc_cwd`) has been REMOVED. It deleted on project
    resemblance alone, so a new card in a project could clear the LIVE block of a
    neighboring one — a registry held 5 sessions with the same `cwd` at once. Its one real
    benefit (a killed window) is now covered by the registry: the record left by a killed
    process is removed by the harness itself.
    """
    session_id = hook.get("session_id")
    all_entries = entries()
    if not all_entries:
        # An empty directory does not mean "nothing to do": someone may have just used the
        # `del session-status\*` escape hatch, or TTL may have taken the last entry
        # (`sweep_ttl` never publishes). Then the server holds a set that is not on disk,
        # and the panel paints a card for a block that does not exist. `publish()` checks
        # the marker itself and stays silent when the set agrees — that is, on the vast
        # majority of runs.
        #
        # A missing marker FILE means "nothing was ever announced", and then there is
        # nothing to correct: a machine without a single block in its life never touches
        # this endpoint at all.
        if os.path.exists(POSTED):
            return publish(cfg)
        return
    prefix = ("%s__" % session_id) if session_id else None
    event = hook.get("hook_event_name")
    live, hold_reason = registry_view(event, session_id)
    tails = {}                       # transcript_path -> tail, read ONCE per file
    held_back = 0
    for name, _mtime in all_entries:
        if prefix and name.startswith(prefix):
            drop(name)
            continue
        data = read_entry(name)
        if data is None:
            continue
        if data.get("registry_seen"):
            if live is None:
                held_back += 1
            elif registry_dead(name, live):
                # This entry's session no longer exists, and the entry was created while it
                # WAS in the registry.
                drop(name)
                continue
        # Another session's entry, of a live session, but its own transcript may already
        # carry a resolution — and then only this run can remove it, because that session
        # may fire nothing more.
        if _safe(closed_by_transcript, name, data, tails):
            drop(name)
    if held_back and hold_reason:
        # Without this line "the mechanism died after a change at Anthropic" and "there is
        # nothing to collect" are indistinguishable, and the symptom is exactly the bug this
        # section fixes. A separate key, not `skip`: this is not a run without a measurement,
        # `analyze-samples.py` counts something else there. Logged only when there really
        # was something to hold back.
        log_local({"t": round(time.time(), 3), "alert_skip": hold_reason,
                   "event": event, "wpisy": held_back})
    # UNCONDITIONALLY, not `if hit`: deletions that did not go through here left the server
    # with a set that is not on disk (`sweep_ttl` deletes and does not publish, the `del`
    # escape hatch deletes from outside the probe). `publish()` compares the snapshot with
    # the marker, so a POST goes out only on a real divergence, not on every event.
    #
    # Only in the SWEEP branch (four events). `leave()` stays on `if not entries()`, because
    # `PostToolUse` fires on EVERY tool call and that is the hot path.
    publish(cfg)


def alert_shutdown(cfg):
    """Disabled by `session_status: false` — clear whatever is still hanging.

    A bare `return` would not be enough: a block in progress at the moment of disabling
    would stay on the panel until the server TTL (24 h), because nobody would send a
    correction any more. After the first such run the directory is empty and every
    following one ends at the `scandir` alone.
    """
    current = entries()
    if not current:
        return
    for name, _mtime in current:
        drop(name)
    publish(cfg)


def alert_dispatch(cfg, hook):
    """The alert section's entry point. Called BEFORE the throttle, wrapped in `_safe`."""
    if not cfg.get("session_status", True):
        return alert_shutdown(cfg)

    event = hook.get("hook_event_name")

    if event == "PreToolUse":
        # The tool-name gate as the FIRST statement: for ~90% of calls the branch ends
        # here and never touches the disk.
        reason = ENTER_TOOLS.get(hook.get("tool_name"))
        if reason is None:
            return
        return enter(cfg, hook, reason, hook.get("tool_use_id"))

    if event == "PermissionRequest":
        if hook.get("tool_name") in ENTER_TOOLS:
            # These two have their own entry by `tool_use_id`. The order of PreToolUse vs
            # PermissionRequest is NOT GUARANTEED (measured 20% inversions), so two entry
            # sources for one call would give a race over two files.
            return
        return enter(cfg, hook, "permission",
                     call_key(hook.get("tool_name"), hook.get("tool_input"),
                              hook.get("prompt_id")))

    if event in CLOSING_EVENTS or event == "PostToolBatch":
        return leave(cfg, hook)

    if event == "PermissionDenied":
        # Registered for the whole measurement and never fired once — no rule may rest
        # on it. Wired in because it costs nothing and will catch classifier denials.
        return leave(cfg, hook)

    if event in SWEEP_EVENTS or event == "SessionStart":
        _safe(sweep_ttl, cfg.get("blocked_ttl_sec", DEFAULT_TTL_S), time.time())
        # `SessionStart` stays on the list, because it sweeps its own prefix and counts
        # TTL. The registry death rule is NOT used on it — see `registry_view`.
        return sweep_session(cfg, hook)


# --------------------------------------------------------------- main
def main():
    t0 = time.perf_counter()

    # The barrier against recursion. MUST come before anything else: the child process
    # `claude -p "/usage"` fires the Stop hook, which starts this probe again.
    if os.environ.get(CHILD_ENV):
        return 0

    # The hook payload arrives in UTF-8, but `sys.stdin` in text mode decodes it with the
    # locale encoding (cp1250 here) and `errors=surrogateescape`. There were two effects,
    # both silent: Polish characters reached the toast and the panel in doubled form, with
    # one character becoming two, and bytes with no counterpart in cp1250 (0x81 0x83 0x88 0x90 0x98 — that is,
    # among others, "L" with a stroke and the typographic apostrophe) became lone
    # surrogates, which broke `write_excl` on `.encode("utf-8")`. The block entry was then
    # created EMPTY, so the alert went nowhere while the key was already taken. The rest
    # of the path has explicit utf-8, so it faithfully carried whatever entered here — one
    # place ruined all of them.
    # A `lambda`, so that `_safe` also shields the attribute access (stdin can be None).
    data = _safe(lambda: sys.stdin.buffer.read().decode("utf-8", "replace")) or "{}"
    hook = _safe(json.loads, data) or {}
    cfg = load_config()

    # BEFORE the throttle. An alert has to arrive immediately, and the probe throttle is
    # 60 s — past that threshold a block would become visible only after a minute, or not
    # at all. `_safe`, because of AGENTS.md rule 3: the probe must not raise, and the signaller must
    # not spoil the limit measurement.
    _safe(alert_dispatch, cfg, hook)

    if throttled(int(cfg.get("throttle_sec", 60))):
        return 0

    fresh, fresh_at, fresh_skip = read_fresh()
    acct, cached, cfg_dir, eu_reason = read_claude_json()

    # A measurement is ordered for the NEXT cycle. Always, including when there is nothing
    # to send now — this is precisely the bootstrap mechanism on a fresh machine.
    spawn_err = spawn_refresh(cfg)

    if not cached or not isinstance(cached.get("utilization"), dict):
        # The first run on a machine: Claude Code has never written the cache yet.
        # The spawn above will fix it, the measurement appears in the next cycle.
        log_local({"t": round(time.time(), 3), "ok": False, "skip": "brak-cache",
                   "spawn": spawn_err, "event": hook.get("hook_event_name")})
        return 0

    cache_at = (cached.get("fetchedAtMs") or 0) / 1000.0
    cache_age = time.time() - cache_at
    if cache_age > CACHE_MAX_AGE_S:
        log_local({"t": round(time.time(), 3), "ok": False, "skip": "cache-przeterminowany",
                   "cache_age_s": round(cache_age), "spawn": spawn_err})
        return 0

    # Claude Code clears the cache on an account change, but that is not taken on trust.
    if acct and cached.get("accountUuid") and cached["accountUuid"] != acct.get("accountUuid"):
        log_local({"t": round(time.time(), 3), "ok": False, "skip": "cache-innego-konta"})
        return 0

    if fresh and dump_outdated(fresh_at, cache_at):
        fresh, fresh_skip = None, "zrzut-starszy-od-cache"

    usage, covered = merge(cached["utilization"], fresh)
    usage, dropped = sanitize(usage, covered, time.time())

    # captured_at is the moment of MEASUREMENT, never now(). Starting the probe confirms
    # nothing — reading a 4-minute-old cache only means the value was that 4 minutes ago.
    # Substituting now() here would overstate freshness in the UI.
    #
    # ALWAYS `cache_at`, never a blend with `fresh_at`. A measurement is made of TWO sources
    # of different age, and `spend` and `extra_usage` come ONLY from the cache — a single
    # stamp taken from the dump rejuvenated them by the whole age difference (up to an
    # hour). The backend decides "which reading is current" by it, so a machine with an
    # older cache but a fresher dump rolled back the state of `spend:org` and `extra:usage`
    # — the only two series that never have a window boundary, so the monotonicity guard
    # does not defend them.
    #
    # The dump's age travels separately (`fresh_at` + `fresh_covered`); it is the backend
    # that assembles a per-series date from it, because only it knows which series is what.
    #
    # Separating "the first observation of this value" (the chart) from "the last
    # confirmation" (freshness) is NOT done here: the probe does not know a series' previous
    # value. The backend does it, holding series_state — see confirmed_at in
    # backend/app/services/ingest.py.
    captured = cache_at

    # As in post(): a local import, because this point is already past the throttle. Do NOT
    # move it up — a use before this line is an UnboundLocalError, which the `except
    # Exception` at :1766 swallows into silence and the machine stops reporting without
    # a single symptom.
    import socket, hashlib

    record = {
        "account": {
            "uuid": (acct or {}).get("accountUuid"),
            "email": (acct or {}).get("emailAddress"),
            "display_name": (acct or {}).get("displayName"),
            "org_uuid": (acct or {}).get("organizationUuid"),
            "org_name": (acct or {}).get("organizationName"),
            "org_type": (acct or {}).get("organizationType"),
            "seat_tier": (acct or {}).get("seatTier"),
            "org_rate_limit_tier": (acct or {}).get("organizationRateLimitTier"),
            "user_rate_limit_tier": (acct or {}).get("userRateLimitTier"),
            "extra_usage_enabled": (acct or {}).get("hasExtraUsageEnabled"),
        },
        "token_meta": load_token_meta(),
        "captured_at": _iso(captured),
        "client": {
            "host": _safe(socket.gethostname),
            "config_dir_hash": (hashlib.sha256(cfg_dir.encode()).hexdigest()[:16]
                                if cfg_dir else None),
            "script_version": SCRIPT_VERSION,
            "exec_ms": round((time.perf_counter() - t0) * 1000),
        },
        "hook": {"event": hook.get("hook_event_name"),
                 "session_id": hook.get("session_id"), "cwd": hook.get("cwd")},
        "measurement": {
            "source": "cli_merged" if fresh else "cli_usage_cache",
            "probe_at": _iso(time.time()),  # when the probe started — DIAGNOSTICS, not to
                                            # be confused with captured_at; the difference
                                            # between the two is the measurement lag
            "cache_age_s": round(cache_age),
            # The reason from the CLIENT cache. DIAGNOSTICS: it tells an exhausted own pool
            # apart from the organization ceiling, but the backend's verdict rests on
            # `usage.spend`, because only that is consistent with the rest of the same
            # response. It does not go into `usage` — parse_usage iterates the top-level
            # keys there and a non-dict value would report schema drift on every measurement.
            "extra_usage_disabled_reason": eu_reason,
            "fresh_age_s": round(time.time() - fresh_at) if fresh_at else None,
            # The dump's time and the list of series that took their value from it. The
            # backend dates THOSE series by them, and the rest by `captured_at` (i.e. by
            # the cache). The identifiers must agree with `parsing.probe_key` — see
            # `_limit_key`.
            "fresh_at": _iso(fresh_at) if (fresh and fresh_at) else None,
            "fresh_covered": covered,
            "fresh_skip": fresh_skip,       # why the stdout dump was not used
            "dropped": dropped,             # what sanitize rejected, and why
            "spawn_error": spawn_err,
        },
        "usage": usage,
    }

    log_local(dict(record, t=round(time.time(), 3), ok=True))   # local log regardless of POST

    if not cfg.get("ingest_url") or not cfg.get("ingest_token"):
        return 0                                   # "local only" mode — no configuration

    backlog, spool_total = read_spool(MAX_BACKLOG_PER_REQUEST)

    # THE AGE ANCHOR. The backend computes `offset = arrived_at - sent_at` from it and dates
    # the measurement as `min(ts + offset, arrived_at)`, that is `received_at - age`. The
    # machine's wall clock does not enter the arithmetic — only the difference `sent_at - ts`
    # counts, and that one is within a SINGLE clock and therefore trustworthy.
    #
    # Set just before sending and written ALSO into `record`, which goes to the spool: for
    # a spooled entry `sent_at` comes from the moment of the failed attempt, so the age
    # comes out right on its own, with nothing to recompute on the client side.
    record["measurement"]["sent_at"] = _iso(time.time())

    payload = dict(record)
    if backlog:
        payload["backlog"] = backlog

    try:
        code, resp = post(cfg, cfg["ingest_url"], payload)
    except Exception:
        append_spool(record)
        return 0

    if code >= 300:
        append_spool(record)
        return 0

    parsed = _safe(json.loads, resp) or {}
    trim_spool(int(parsed.get("backlogAccepted") or parsed.get("backlog_accepted") or 0))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)          # the probe has no right to break the session
