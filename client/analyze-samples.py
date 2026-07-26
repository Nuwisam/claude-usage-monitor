#!/usr/bin/env python3
"""Analiza probek z usage-probe.py — odpowiada na pytania, ktorych nie da sie
rozstrzygnac inaczej niz obserwacja:

  A. jak szybko realnie zmienia sie utilization  -> dobor throttle i sens historii
  B. czy dedup ma sens i ile wycina
  C. czy endpoint kiedykolwiek zwraca 429 / blad przy naszym tempie
  D. ktore buckety sa niepuste i czy pojawiaja sie nowe
  E. czy is_active sie przelacza (co realnie ogranicza)
  F. przelaczenia konta i roznice miedzy planami

Uzycie: python analyze-samples.py [sciezka.jsonl]
"""
import sys, os, json, time
from collections import Counter, defaultdict, OrderedDict


def default_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/.local/state")
    return os.path.join(base, "claude-usage-monitor", "usage-samples.jsonl")


def pctile(xs, q):
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(q * (len(s) - 1))))]


def ts(t):
    return time.strftime("%H:%M:%S", time.localtime(t))


def iso_to_epoch(v):
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.replace("Z", "+00:00")
    if "." in s:                                  # mikrosekundy -> 6 cyfr
        head, rest = s.split(".", 1)
        frac = ""
        i = 0
        while i < len(rest) and rest[i].isdigit():
            frac += rest[i]; i += 1
        s = head + "." + (frac + "000000")[:6] + rest[i:]
    try:
        from datetime import datetime
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else default_path()
    if not os.path.isfile(path):
        print("BRAK PLIKU: %s\nSonda jeszcze nie zebrala danych." % path)
        return 1

    recs = []
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line:
            try:
                recs.append(json.loads(line))
            except Exception:
                pass
    if not recs:
        print("Plik pusty.")
        return 1

    span = recs[-1]["t"] - recs[0]["t"]
    ok = [r for r in recs if r.get("ok")]
    print("=" * 76)
    print("PROBKI: %d   udanych: %d   okres: %s - %s (%.1f min)"
          % (len(recs), len(ok), ts(recs[0]["t"]), ts(recs[-1]["t"]), span / 60))
    print("=" * 76)

    # ---- C. bledy i rate limiting -----------------------------------------
    print("\n[C] kody odpowiedzi i bledy")
    print("    http: %s" % dict(Counter(str(r.get("http")) for r in recs)))
    skips = Counter(r.get("skip") for r in recs if r.get("skip"))
    if skips:
        print("    pominiete: %s" % dict(skips))
    errs = Counter(r.get("error") for r in recs if r.get("error"))
    if errs:
        print("    bledy transportu: %s" % dict(errs))
    r429 = [r for r in recs if r.get("http") == 429]
    print("    429: %d %s" % (len(r429), "<-- ZWOLNIC TEMPO" if r429 else "(brak)"))
    rl = Counter(r.get("rl_status") for r in recs if r.get("rl_status"))
    if rl:
        print("    anthropic-ratelimit-unified-status: %s" % dict(rl))
    ms = [r["ms"] for r in recs if isinstance(r.get("ms"), (int, float))]
    if ms:
        print("    czas wywolania: p50=%s ms  p95=%s ms  max=%s ms"
              % (pctile(ms, .5), pctile(ms, .95), max(ms)))
    gaps = [recs[i]["t"] - recs[i-1]["t"] for i in range(1, len(recs))]
    if gaps:
        print("    odstepy miedzy zapisami: min=%.0f s  p50=%.0f s  (throttle dziala jesli min >= throttle)"
              % (min(gaps), pctile(gaps, .5)))
        print("    tempo: %.1f wywolan/godzine" % (3600.0 * len(recs) / span if span > 0 else 0))

    # ---- D. buckety --------------------------------------------------------
    print("\n[D] klucze najwyzszego poziomu w odpowiedzi")
    keys, nonnull = Counter(), Counter()
    for r in ok:
        u = r.get("usage") or {}
        if isinstance(u, dict):
            for k, v in u.items():
                keys[k] += 1
                if v is not None:
                    nonnull[k] += 1
    for k in sorted(keys):
        flag = "  <- NIEPUSTY" if nonnull[k] else ""
        print("    %-30s widziany=%-4d niepusty=%-4d%s" % (k, keys[k], nonnull[k], flag))

    # ---- A/B. dynamika utilization ----------------------------------------
    print("\n[A/B] dynamika wartosci — to decyduje o throttle i o sensie historii")
    series = defaultdict(list)
    for r in ok:
        u = r.get("usage") or {}
        if not isinstance(u, dict):
            continue
        for k, v in u.items():
            if isinstance(v, dict) and "utilization" in v:
                series["bucket:%s" % k].append((r["t"], v.get("utilization"), v.get("resets_at")))
        for l in (u.get("limits") or []):
            sc = l.get("scope") or {}
            mdl = ((sc.get("model") or {}).get("display_name")) if sc else None
            name = "limit:%s%s" % (l.get("kind"), "/" + mdl if mdl else "")
            series[name].append((r["t"], l.get("percent"), l.get("resets_at")))

    for key in sorted(series):
        pts = [p for p in series[key] if p[1] is not None]
        if not pts:
            continue
        ch = [pts[i] for i in range(1, len(pts))
              if (pts[i][1], pts[i][2]) != (pts[i-1][1], pts[i-1][2])]
        vals = [p[1] for p in pts]
        line = "    %-26s n=%-4d zmian=%-4d zakres=%.1f..%.1f%%" % (
            key, len(pts), len(ch), min(vals), max(vals))
        if len(ch) >= 2:
            iv = [ch[i][0] - ch[i-1][0] for i in range(1, len(ch))]
            line += "  mediana odstepu zmian=%.0f s" % pctile(iv, .5)
        print(line)
        if pts:
            print("        dedup wycialby %.0f%% wierszy" % (100.0 * (1 - len(ch) / len(pts))))
        # tempo spalania
        if len(pts) >= 2 and span > 0:
            d = vals[-1] - vals[0]
            h = (pts[-1][0] - pts[0][0]) / 3600.0
            if h > 0.05:
                rate = d / h
                extra = ""
                if rate > 0.01:
                    left = (100.0 - vals[-1]) / rate
                    extra = "  -> do 100%% ok. %.1f h przy tym tempie" % left
                    e = iso_to_epoch(pts[-1][2])
                    if e:
                        extra += " (reset za %.1f h)" % ((e - pts[-1][0]) / 3600.0)
                print("        tempo: %+.2f pp/h%s" % (rate, extra))

    # ---- E. co realnie ogranicza ------------------------------------------
    print("\n[E] ktory limit jest wiazacy (is_active)")
    act = Counter()
    for r in ok:
        u = r.get("usage") or {}
        for l in (u.get("limits") or []):
            if l.get("is_active"):
                act["%s (%s)" % (l.get("kind"), l.get("group"))] += 1
    print("    %s" % (dict(act) or "brak danych"))
    sev = Counter()
    for r in ok:
        for l in ((r.get("usage") or {}).get("limits") or []):
            if l.get("severity"):
                sev[l["severity"]] += 1
    if sev:
        print("    severity: %s" % dict(sev))

    # ---- F. konta ----------------------------------------------------------
    print("\n[F] konta")
    accs = OrderedDict()
    for r in recs:
        a = r.get("account")
        if a and a.get("accountUuid"):
            accs.setdefault(a["accountUuid"], a)
    for uuid, a in accs.items():
        print("    %s  %-28s org=%-14s seat=%-9s tier=%s"
              % (uuid[:8], a.get("emailAddress"), a.get("organizationType"),
                 a.get("seatTier"), a.get("organizationRateLimitTier")))
    sw, prev = [], None
    for r in recs:
        u = (r.get("account") or {}).get("accountUuid")
        if u and prev and u != prev:
            sw.append((r["t"], prev, u))
        if u:
            prev = u
    print("    przelaczen konta: %d %s" % (len(sw), [ts(t) for t, _, _ in sw] if sw else ""))
    print("\n" + "=" * 76)
    return 0


if __name__ == "__main__":
    sys.exit(main())
