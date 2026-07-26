#!/usr/bin/env python3
"""Sonda limitow Claude, uruchamiana z hooka Claude Code (PostToolUse async + Stop).

Czyta biezacy token lokalnie, odpytuje /api/oauth/usage i wysyla WYNIK do monitora.
Token nigdy nie opuszcza maszyny.

ZASADY BEZPIECZENSTWA — nie lamac przy rozwijaniu:
  1. .credentials.json TYLKO do odczytu. Nigdy nie zapisujemy.
  2. NIGDY nie wolamy endpointu tokenowego (grant_type=refresh_token). Refresh nalezy do
     Claude Code. Wygasly token => pomijamy pomiar. To usuwa glowny wektor utraty konta:
     rotacje jednorazowego refresh tokenu.
  3. Token nie trafia do logu ani do monitora.
  4. Throttle obowiazkowy — PostToolUse odpala sie przy kazdym narzedziu, a wywolanie
     sieciowe kosztuje ~500 ms.
  5. Zero ciezkich importow. Tylko stdlib; http.client zamiast httpx/requests (~150 ms
     samego importu). Zmierzony start CPythona: 27 ms — caly budzet zalezy od tej zasady.
  6. Nigdy nie rzuca wyjatkiem i nigdy nie blokuje sesji.

Konfiguracja: %LOCALAPPDATA%\\claude-usage-monitor\\config.json (Windows)
              ~/.local/state/claude-usage-monitor/config.json (Linux)
    {"ingest_url": "https://usage.example.org/claude-usage/api/ingest",
     "ingest_token": "<token TEJ maszyny>",
     "edge_key": "<wspolny sekret brzegowy>",
     "throttle_sec": 60}
Celowo plik lokalny, a nie repo — token maszyny nie ma prawa trafic do gita.
"""
import sys, os, json, time, socket, hashlib, http.client, urllib.parse

SCRIPT_VERSION = 2
UA_VERSION = os.environ.get("CLAUDE_CODE_UA_VERSION", "2.1.215")
ANTHROPIC_HOST, ANTHROPIC_PATH = "api.anthropic.com", "/api/oauth/usage"
BETA = "oauth-2025-04-20"

_base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/state")
OUTDIR = os.path.join(_base, "claude-usage-monitor")
CONFIG = os.path.join(OUTDIR, "config.json")
LOG = os.path.join(OUTDIR, "usage-samples.jsonl")
SPOOL = os.path.join(OUTDIR, "spool.jsonl")
THROTTLE_FILE = os.path.join(OUTDIR, "last-probe.txt")
ACCT_CACHE = os.path.join(OUTDIR, "oauth-account.cache.json")

MAX_SPOOL_LINES = 5000
MAX_BACKLOG_PER_REQUEST = 200


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
    """Znacznik zapisujemy PRZED wywolaniem, zeby rownolegle hooki nie zrobily stampede."""
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


# --------------------------------------------------------------- tozsamosc konta
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
    """Wycina zbalansowany blok {...} po kluczu. Odporne na duplikaty kluczy roznjace sie
    wielkoscia liter (z:/... i Z:/...), na ktorych parsery calego pliku padaja."""
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


_ACCT_FIELDS = ("accountUuid", "emailAddress", "organizationUuid", "organizationName",
                "organizationType", "organizationRateLimitTier", "userRateLimitTier",
                "seatTier", "hasExtraUsageEnabled", "displayName")


def account_info():
    """Cache po mtime. Przelaczenie konta przez /login przepisuje .claude.json, wiec
    inwalidacja po mtime JEST mechanizmem wykrywania przelaczenia."""
    path = _find(".claude.json")
    if not path:
        return None, None
    try:
        mtime = os.path.getmtime(path)
        with open(ACCT_CACHE, "r", encoding="utf-8") as f:
            c = json.load(f)
        if c.get("mtime") == mtime and c.get("path") == path:
            return c.get("account"), os.path.dirname(path)
    except Exception:
        pass
    acct = None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            raw = _extract_block(f.read(), "oauthAccount")
        if raw:
            acct = {k: raw.get(k) for k in _ACCT_FIELDS}
        os.makedirs(OUTDIR, exist_ok=True)
        with open(ACCT_CACHE, "w", encoding="utf-8") as f:
            json.dump({"path": path, "mtime": os.path.getmtime(path), "account": acct}, f)
    except Exception:
        pass
    return acct, os.path.dirname(path)


# --------------------------------------------------------------- token (read-only)
def load_token():
    path = _find(".credentials.json", in_claude_dir=True)
    if not path:
        return None, {"reason": "brak-credentials"}
    try:
        with open(path, "r", encoding="utf-8") as f:      # TYLKO odczyt
            oa = (json.load(f).get("claudeAiOauth") or {})
    except Exception as e:
        return None, {"reason": "odczyt-%s" % type(e).__name__}
    exp = oa.get("expiresAt")
    meta = {"subscription_type": oa.get("subscriptionType"),
            "rate_limit_tier": oa.get("rateLimitTier"),
            "expires_in_s": int(exp / 1000.0 - time.time()) if exp else None}
    if exp and exp / 1000.0 < time.time():
        return None, dict(meta, reason="token-wygasl")     # NIE odswiezamy — to robi Claude Code
    return oa.get("accessToken"), meta


def fetch_usage(token):
    conn = http.client.HTTPSConnection(ANTHROPIC_HOST, timeout=8.0)
    try:
        conn.request("GET", ANTHROPIC_PATH, headers={
            "Authorization": "Bearer %s" % token,
            "anthropic-beta": BETA,
            "User-Agent": "claude-code/%s" % UA_VERSION,   # bez tego agresywny bucket 429
            "Content-Type": "application/json",
            "Accept": "application/json"})
        r = conn.getresponse()
        body = r.read().decode("utf-8", "replace")
        return r.status, {k.lower(): v for k, v in r.getheaders()}, body
    finally:
        _safe(conn.close)


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
            with open(SPOOL, "w", encoding="utf-8") as f:   # wyrzucamy najstarsze
                f.write("\n".join(lines[-MAX_SPOOL_LINES:]) + "\n")
    except Exception:
        pass


def trim_spool(accepted):
    """Obcinamy DOPIERO po potwierdzeniu, ile wpisow przyjeto — awaria w polowie nie gubi danych."""
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
    """Magazyn CA Windows uzywany przez Pythona odrzuca lancuch Let's Encrypt niektorych hostow
    z bledem 'certificate has expired', mimo ze KAZDE ogniwo jest wazne (zweryfikowane
    openssl-em: 2026/2028/2032/2032). curl przechodzi, Python nie — czyli wina magazynu,
    nie serwera. certifi dziala, wiec uzywamy go, gdy jest dostepny.

    Swiadomie NIE wylaczamy weryfikacji. `ca_bundle` w config.json pozwala wskazac wlasny
    plik, gdyby certifi nie bylo zainstalowane."""
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


def post(cfg, body):
    url = urllib.parse.urlsplit(cfg["ingest_url"])
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


# --------------------------------------------------------------- main
def main():
    t0 = time.perf_counter()
    hook = _safe(json.loads, sys.stdin.read() or "{}") or {}
    cfg = load_config()

    if throttled(int(cfg.get("throttle_sec", 60))):
        return 0

    tok, meta = load_token()
    if not tok:
        log_local({"t": round(time.time(), 3), "ok": False, "skip": meta.get("reason"),
                   "event": hook.get("hook_event_name")})
        return 0

    try:
        status, hdrs, body = fetch_usage(tok)
    except Exception as e:
        log_local({"t": round(time.time(), 3), "ok": False, "error": type(e).__name__,
                   "event": hook.get("hook_event_name")})
        return 0

    usage = _safe(json.loads, body)
    acct, cfg_dir = account_info()

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
        "token_meta": meta,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
        "client": {
            "host": _safe(socket.gethostname),
            "config_dir_hash": (hashlib.sha256(cfg_dir.encode()).hexdigest()[:16]
                                if cfg_dir else None),
            "cc_version": UA_VERSION, "script_version": SCRIPT_VERSION,
            "exec_ms": round((time.perf_counter() - t0) * 1000),
        },
        "hook": {"event": hook.get("hook_event_name"),
                 "session_id": hook.get("session_id"), "cwd": hook.get("cwd")},
        "http": {"status": status, "request_id": hdrs.get("request-id"),
                 "retry_after": hdrs.get("retry-after"),
                 "rl_status": hdrs.get("anthropic-ratelimit-unified-status"),
                 "cf_ray": hdrs.get("cf-ray")},
        "usage": usage if usage is not None else body[:2000],
    }

    log_local(dict(record, t=round(time.time(), 3), ok=status == 200))     # lokalny log zostaje niezaleznie od POST

    if not cfg.get("ingest_url") or not cfg.get("ingest_token"):
        return 0                                   # tryb "tylko lokalnie" — brak konfiguracji

    backlog, spool_total = read_spool(MAX_BACKLOG_PER_REQUEST)
    payload = dict(record)
    if backlog:
        payload["backlog"] = backlog

    try:
        code, resp = post(cfg, payload)
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
        sys.exit(0)          # sonda nie ma prawa zepsuc sesji
