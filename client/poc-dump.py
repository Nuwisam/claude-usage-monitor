#!/usr/bin/env python3
"""Krok 0 PoC — loguje lokalnie to, co REALNIE przychodzi ze statusline Claude Code.

Zero sieci, zero bazy, zero zaleznosci. Odpowiada na 8 pytan z planu:
  1. czy rate_limits w ogole przychodzi i jakie jest rate_limits_available
  2. jaka jest wartosc subscription_type
  3. dokladny zestaw bucketow w rate_limits
  4. czy model_scoped jest obecne i jaki ma format resets_at (ISO vs epoch)
  5. jak czesto statusline realnie odpala (znaczniki t)
  6. czy i jak czesto wartosci rate_limits sie zmieniaja w trakcie sesji
  7. exec_ms na tej maszynie (p50/p95)
  8. czy oauthAccount da sie wyciac z .claude.json i ile to kosztuje (oauth_ms)

ZASADY, ktorych nie wolno zlamac przy rozwijaniu tego skryptu:
  - print() PRZED jakimkolwiek I/O. Statusline ma dzialac nawet gdy reszta padnie.
  - zero ciezkich importow. httpx/requests/urllib to ~150 ms startu. Budzet to 300 ms
    (debounce Claude Code), a skrypt w locie jest ANULOWANY przy nowej aktualizacji.
  - nigdy nie rzucaj wyjatkiem.
  - log NIE moze trafic do repo git — zawiera sciezki projektow, nazwy sesji i koszty.
"""
import sys, json, os, time, re

T0 = time.perf_counter()

# ---------------------------------------------------------------- wejscie
try:
    payload = json.loads(sys.stdin.read())
except Exception:
    payload = {}

# ---------------------------------------------------------------- statusline (NAJPIERW)
def _pct(bucket):
    v = (bucket or {}).get("used_percentage")
    if not isinstance(v, (int, float)):
        return "?"
    return "%.0f%%" % v

def _eta(bucket):
    r = (bucket or {}).get("resets_at")
    if not isinstance(r, (int, float)):
        return ""
    left = int(r - time.time())
    if left < 0:
        return " (po resecie)"
    return " (%dh%02dm)" % (left // 3600, (left % 3600) // 60)

rl = payload.get("rate_limits") or {}
try:
    print("5h %s%s | 7d %s%s | %s | avail=%s | ctx %s%%" % (
        _pct(rl.get("five_hour")), _eta(rl.get("five_hour")),
        _pct(rl.get("seven_day")), _eta(rl.get("seven_day")),
        payload.get("subscription_type") or "brak-sub",
        payload.get("rate_limits_available"),
        (payload.get("context_window") or {}).get("used_percentage"),
    ))
except Exception:
    print("claude-usage-monitor poc")

# ---------------------------------------------------------------- katalog na dane
# Celowo POZA repo git i POZA CLAUDE_CONFIG_DIR.
_base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/state")
OUTDIR = os.path.join(_base, "claude-usage-monitor")

# ---------------------------------------------------------------- pytanie 8: oauthAccount
# .claude.json ma >1200 linii i DUPLIKATY kluczy roznjace sie wielkoscia liter
# (z:/projects/... i Z:/projects/...), na ktorych ConvertFrom-Json w PowerShellu sie wywala.
# Wycinamy sam blok oauthAccount regexem — omija problem i nie parsuje calego pliku.
_OAUTH_RE = re.compile(r'"oauthAccount"\s*:\s*\{')

def _find_claude_json():
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    cands = []
    if cfg:
        cands.append(os.path.join(cfg, ".claude.json"))
    cands.append(os.path.expanduser("~/.claude.json"))
    for p in cands:
        if os.path.isfile(p):
            return p
    return None

def _extract_oauth_account(path):
    """Zwraca dict albo None. Wycina zbalansowany blok {...} po kluczu oauthAccount."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    m = _OAUTH_RE.search(text)
    if not m:
        return None
    start = m.end() - 1          # pozycja '{'
    depth, i, n = 0, start, len(text)
    in_str, esc = False, False
    while i < n:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
        i += 1
    return None

def _oauth_account_cached():
    """Cache po mtime — przelaczenie konta przez /login przepisuje .claude.json,
    wiec inwalidacja po mtime JEST mechanizmem wykrywania przelaczenia."""
    path = _find_claude_json()
    if not path:
        return None, "brak-pliku", False
    mtime = os.path.getmtime(path)
    cache_path = os.path.join(OUTDIR, "oauth-account.cache.json")
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("mtime") == mtime and cached.get("path") == path:
            return cached.get("account"), "cache", False
    except Exception:
        pass
    acct = _extract_oauth_account(path)
    try:
        os.makedirs(OUTDIR, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump({"path": path, "mtime": mtime, "account": acct}, f)
    except Exception:
        pass
    return acct, "swiezy-odczyt", True

t_oauth = time.perf_counter()
try:
    account, oauth_src, was_reread = _oauth_account_cached()
except Exception as e:
    account, oauth_src, was_reread = None, "blad:%s" % type(e).__name__, False
oauth_ms = round((time.perf_counter() - t_oauth) * 1000, 2)

# Nie logujemy calego oauthAccount — tylko pola, ktorych naprawde potrzebuje projekt.
acct_slim = None
if isinstance(account, dict):
    acct_slim = {k: account.get(k) for k in (
        "accountUuid", "emailAddress", "organizationUuid", "organizationName",
        "organizationType", "organizationRateLimitTier", "userRateLimitTier",
        "seatTier", "hasExtraUsageEnabled", "billingType", "displayName",
    )}

# ---------------------------------------------------------------- zapis
record = {
    "t": round(time.time(), 3),
    "exec_ms": None,                 # uzupelniane nizej
    "oauth_ms": oauth_ms,
    "oauth_src": oauth_src,
    "oauth_reread": was_reread,
    "account": acct_slim,
    "payload": payload,              # CALY surowy payload — o to chodzi w kroku 0
}
record["exec_ms"] = round((time.perf_counter() - T0) * 1000, 2)

try:
    os.makedirs(OUTDIR, exist_ok=True)
    with open(os.path.join(OUTDIR, "poc-statusline.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
except Exception:
    pass
