#!/usr/bin/env python3
"""Analysis of samples from usage-probe.py — it answers the questions that cannot be
settled in any way other than by observation:

  A. how fast utilization really changes  -> the throttle choice and the point of history
  B. whether dedup makes sense and how much it cuts
  C. where the measurement comes from and whether `/usage` stdout actually arrives
  D. which buckets are non-empty and whether new ones appear
  E. whether is_active toggles (what really constrains)
  F. account switches and the differences between plans

Usage: python analyze-samples.py [path.jsonl]
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


def meas(r):
    """The `measurement` block from a log entry. Entries older than probe version 3 lack it."""
    m = r.get("measurement")
    return m if isinstance(m, dict) else {}


def iso_to_epoch(v):
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.replace("Z", "+00:00")
    if "." in s:                                  # microseconds -> 6 digits
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
        print("NO FILE: %s\nThe probe has not collected any data yet." % path)
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
        print("File empty.")
        return 1

    span = recs[-1]["t"] - recs[0]["t"]
    ok = [r for r in recs if r.get("ok")]
    print("=" * 76)
    print("SAMPLES: %d   successful: %d   period: %s - %s (%.1f min)"
          % (len(recs), len(ok), ts(recs[0]["t"]), ts(recs[-1]["t"]), span / 60))
    print("=" * 76)

    # ---- C. measurement provenance ----------------------------------------
    # Version 3 of the probe sends no request to Anthropic at all, so there are no HTTP
    # codes here, no 429 and no rate-limit headers. We watch something else: WHERE the
    # measurement came from.
    #
    # `cli_merged`      — fresh percentages arrived from `/usage` stdout (minute resolution)
    # `cli_usage_cache` — the Claude Code cache alone went out, which can be 5 minutes old
    #
    # A predominance of the second is a SILENT failure: the data still flows and nothing
    # looks broken, only the resolution dropped from a minute to five. This is the only
    # place where that shows plainly — which is why the `cli_merged` share is a number
    # here, not merely a distribution.
    print("\n[C] measurement provenance")
    src = Counter(meas(r).get("source") for r in ok if meas(r).get("source"))
    if src:
        total = sum(src.values())
        merged = src.get("cli_merged", 0)
        print("    source: %s" % dict(src))
        print("    fresh from stdout: %d/%d (%.0f%%)%s"
              % (merged, total, 100.0 * merged / total,
                 "   <-- CHECK `claude -p /usage`" if merged * 2 < total else ""))
    skips = Counter(r.get("skip") for r in recs if r.get("skip"))
    if skips:
        print("    runs without a measurement: %s" % dict(skips))
    fskip = Counter(meas(r).get("fresh_skip") for r in ok if meas(r).get("fresh_skip"))
    if fskip:
        print("    stdout dump rejected: %s" % dict(fskip))
    spawn = Counter(meas(r).get("spawn_error") or r.get("spawn")
                    for r in recs if meas(r).get("spawn_error") or r.get("spawn"))
    if spawn:
        print("    could not order a measurement: %s" % dict(spawn))
    dropped = Counter()
    for r in ok:
        for d in (meas(r).get("dropped") or []):
            dropped[str(d).split("(")[0]] += 1
    if dropped:
        print("    rejected by the guards: %s" % dict(dropped))

    for name, path in (("cache age", ("measurement", "cache_age_s")),
                       ("stdout age", ("measurement", "fresh_age_s")),
                       ("hook time [ms]", ("client", "exec_ms"))):
        xs = [r[path[0]][path[1]] for r in ok
              if isinstance(r.get(path[0]), dict)
              and isinstance(r[path[0]].get(path[1]), (int, float))]
        if xs:
            print("    %-16s p50=%s  p95=%s  max=%s" % (name, pctile(xs, .5),
                                                        pctile(xs, .95), max(xs)))
    gaps = [recs[i]["t"] - recs[i-1]["t"] for i in range(1, len(recs))]
    if gaps:
        print("    gaps between writes: min=%.0f s  p50=%.0f s  (throttle works if min >= throttle)"
              % (min(gaps), pctile(gaps, .5)))
        print("    rate: %.1f calls/hour" % (3600.0 * len(recs) / span if span > 0 else 0))

    # ---- D. buckets --------------------------------------------------------
    print("\n[D] top-level keys in the response")
    keys, nonnull = Counter(), Counter()
    for r in ok:
        u = r.get("usage") or {}
        if isinstance(u, dict):
            for k, v in u.items():
                keys[k] += 1
                if v is not None:
                    nonnull[k] += 1
    for k in sorted(keys):
        flag = "  <- NON-EMPTY" if nonnull[k] else ""
        print("    %-30s seen=%-4d non-empty=%-4d%s" % (k, keys[k], nonnull[k], flag))

    # ---- A/B. utilization dynamics ----------------------------------------
    print("\n[A/B] value dynamics — this decides the throttle and the point of history")
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
        line = "    %-26s n=%-4d changes=%-4d range=%.1f..%.1f%%" % (
            key, len(pts), len(ch), min(vals), max(vals))
        if len(ch) >= 2:
            iv = [ch[i][0] - ch[i-1][0] for i in range(1, len(ch))]
            line += "  median gap between changes=%.0f s" % pctile(iv, .5)
        print(line)
        if pts:
            print("        dedup would cut %.0f%% of rows" % (100.0 * (1 - len(ch) / len(pts))))
        # burn rate
        if len(pts) >= 2 and span > 0:
            d = vals[-1] - vals[0]
            h = (pts[-1][0] - pts[0][0]) / 3600.0
            if h > 0.05:
                rate = d / h
                extra = ""
                if rate > 0.01:
                    left = (100.0 - vals[-1]) / rate
                    extra = "  -> to 100%% in about %.1f h at this rate" % left
                    e = iso_to_epoch(pts[-1][2])
                    if e:
                        extra += " (reset in %.1f h)" % ((e - pts[-1][0]) / 3600.0)
                print("        rate: %+.2f pp/h%s" % (rate, extra))

    # ---- E. what really constrains ----------------------------------------
    print("\n[E] which limit is binding (is_active)")
    act = Counter()
    for r in ok:
        u = r.get("usage") or {}
        for l in (u.get("limits") or []):
            if l.get("is_active"):
                act["%s (%s)" % (l.get("kind"), l.get("group"))] += 1
    print("    %s" % (dict(act) or "no data"))
    sev = Counter()
    for r in ok:
        for l in ((r.get("usage") or {}).get("limits") or []):
            if l.get("severity"):
                sev[l["severity"]] += 1
    if sev:
        print("    severity: %s" % dict(sev))

    # ---- F. accounts -------------------------------------------------------
    print("\n[F] accounts")
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
    print("    account switches: %d %s" % (len(sw), [ts(t) for t, _, _ in sw] if sw else ""))
    print("\n" + "=" * 76)
    return 0


if __name__ == "__main__":
    sys.exit(main())
