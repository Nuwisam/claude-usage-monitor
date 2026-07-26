#!/usr/bin/env python3
"""Sonda: odpytuje GET /api/oauth/usage LOKALNYM tokenem Claude Code.

ZASADY BEZPIECZENSTWA — nie lamac:
  - .credentials.json otwieramy WYLACZNIE do odczytu. Nigdy nie zapisujemy.
  - NIGDY nie wolamy endpointu tokenowego (grant_type=refresh_token).
    Refresh nalezy do Claude Code. My czytamy biezacy accessToken albo pomijamy pomiar.
    To usuwa najwiekszy wektor utraty konta — rotacje jednorazowego refresh tokenu.
  - Token nigdy nie jest logowany ani wysylany dalej. Wysylamy tylko wynik pomiaru.
  - Tylko biblioteka standardowa. http.client jest najlzejsza droga do HTTPS w stdlib;
    httpx/requests to ~150 ms samego importu, a to ma dzialac w hooku.
"""
import sys, os, json, time, http.client

UA_VERSION = os.environ.get("CLAUDE_CODE_UA_VERSION", "2.1.215")
HOST = "api.anthropic.com"
PATH = "/api/oauth/usage"
BETA = "oauth-2025-04-20"


def find_credentials():
    cands = []
    cfg = os.environ.get("CLAUDE_CONFIG_DIR")
    if cfg:
        cands.append(os.path.join(cfg, ".credentials.json"))
    cands.append(os.path.expanduser("~/.claude/.credentials.json"))
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def load_token():
    """Zwraca (access_token, meta) albo (None, meta) — bez ujawniania tokenu."""
    path = find_credentials()
    if not path:
        return None, {"error": "brak .credentials.json"}
    try:
        with open(path, "r", encoding="utf-8") as f:      # TYLKO odczyt
            data = json.load(f)
    except Exception as e:
        return None, {"error": "odczyt: %s" % type(e).__name__}
    oa = data.get("claudeAiOauth") or {}
    tok = oa.get("accessToken")
    exp = oa.get("expiresAt")
    meta = {
        "path": path,
        "subscriptionType": oa.get("subscriptionType"),
        "rateLimitTier": oa.get("rateLimitTier"),
        "scopes": oa.get("scopes"),
        "expiresAt": exp,
        "expired": bool(exp and exp / 1000.0 < time.time()),
        "expires_in_s": int(exp / 1000.0 - time.time()) if exp else None,
    }
    return tok, meta


def fetch_usage(token, timeout=8.0):
    conn = http.client.HTTPSConnection(HOST, timeout=timeout)
    try:
        conn.request("GET", PATH, headers={
            "Authorization": "Bearer %s" % token,
            "anthropic-beta": BETA,
            "User-Agent": "claude-code/%s" % UA_VERSION,   # KRYTYCZNE: bez tego agresywny bucket 429
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        r = conn.getresponse()
        body = r.read().decode("utf-8", "replace")
        hdrs = {k.lower(): v for k, v in r.getheaders()}
        return r.status, hdrs, body
    finally:
        try:
            conn.close()
        except Exception:
            pass


def main():
    tok, meta = load_token()
    print("=== poswiadczenia ===")
    for k in ("path", "subscriptionType", "rateLimitTier", "expired", "expires_in_s"):
        print("  %-18s %s" % (k, meta.get(k)))
    print("  %-18s %s" % ("scopes", meta.get("scopes")))
    if not tok:
        print("BRAK TOKENU: %s" % meta.get("error"))
        return 1
    if meta.get("expired"):
        print("\nToken wygasl. Sonda POMIJA pomiar — nie odswiezamy, to robota Claude Code.")
        return 2

    t0 = time.perf_counter()
    try:
        status, hdrs, body = fetch_usage(tok)
    except Exception as e:
        print("\nBLAD TRANSPORTU: %s: %s" % (type(e).__name__, e))
        return 3
    ms = round((time.perf_counter() - t0) * 1000)

    print("\n=== odpowiedz ===")
    print("  HTTP %s   %d ms   %d B" % (status, ms, len(body)))
    for h in ("request-id", "anthropic-ratelimit-unified-status", "retry-after",
              "content-type", "cf-ray"):
        if h in hdrs:
            print("  %-36s %s" % (h, hdrs[h]))

    print("\n=== body ===")
    try:
        parsed = json.loads(body)
        print(json.dumps(parsed, indent=2, ensure_ascii=False, sort_keys=True))
        if isinstance(parsed, dict):
            print("\n=== klucze najwyzszego poziomu ===")
            for k in sorted(parsed):
                print("  %s" % k)
    except Exception:
        print(body[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
