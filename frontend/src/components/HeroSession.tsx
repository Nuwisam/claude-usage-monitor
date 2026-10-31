import { ClockCountdown } from "@phosphor-icons/react";

import type { AccountStatus, SeriesStatus } from "../api/types";
import { delta, severityLabel } from "../lib/format";
import { describeSeries, resetNote } from "../lib/freshness";
import { parseUtc } from "../lib/time";
import { UtilBar } from "./UtilBar";

/** The 5 h window goes first. The `limits[]` entry before the bucket, because it carries
 *  `isActive` and `severity`. No condition reads the key's content — rule 5 from AGENTS.md. */
export function pickSession(series: SeriesStatus[]): SeriesStatus | null {
  const primary = series.filter((s) => s.primary);
  return (
    primary.find((s) => s.kind === "session") ??
    primary.find((s) => s.bucketKey === "five_hour") ??
    series.find((s) => s.bucketKey === "five_hour") ??
    null
  );
}

/** The all-models week, mirroring `pick_weekly` in panel/panel/view.py. */
export function pickWeekly(series: SeriesStatus[]): SeriesStatus | null {
  const primary = series.filter((s) => s.primary);
  return (
    primary.find((s) => s.kind === "weekly_all") ??
    primary.find((s) => s.bucketKey === "seven_day") ??
    series.find((s) => s.bucketKey === "seven_day") ??
    null
  );
}

export interface Hero {
  s: SeriesStatus | null;
  kicker: string;
  /** What the empty slot names when `s` is null. */
  window: string;
}

/** The hero is pinned to the 5 h session. Were it to jump to follow `isActive`, the same screen
 *  would mean something different depending on the day of the week. The one exception is an
 *  account that HAS no 5 h window (the session rung is `off` — Ultra since 2026-10-31): there
 *  the week is the window you are working in, and it takes the hero for as long as that lasts. */
export function pickHero(a: AccountStatus): Hero {
  if (a.cascade.find((r) => r.key === "session")?.state === "off") {
    return { s: pickWeekly(a.series), kicker: "Week", window: "weekly" };
  }
  return { s: pickSession(a.series), kicker: "Session (5 h)", window: "5-hour" };
}

export function HeroSession({ s, nowMs, kicker }: { s: SeriesStatus; nowMs: number; kicker: string }) {
  const v = describeSeries(s, nowMs);
  const reset = resetNote(s, nowMs);

  return (
    <div className="card elev-sm hero">
      <div className="hero-top">
        <span className="card-kicker" style={{ color: "var(--color-accent-200)" }}>
          {kicker}
        </span>
        {s.isActive && (
          <span className="hero-binds">
            limiting<span className="hero-binds-now"> now</span>
          </span>
        )}
        <span className="hero-fresh">{v.heroNote}</span>
      </div>

      <div className="hero-main">
        <div className="hero-labels">
          <span className="hero-label">{s.label}</span>
          <span className="hero-reset">
            <ClockCountdown size={14} className="ph" />{" "}
            {reset.lead}
            {/* No "at" — the preposition is already in `reset.at`, it changes with the day. */}
            {reset.at && <span className="hero-reset-at"> · {reset.at}</span>}
          </span>
        </div>
        {v.number !== null ? (
          <div className="hero-value">
            <span className="hero-number">{v.number}</span>
            <span className="hero-unit">%</span>
          </div>
        ) : (
          // Never zero — rule 4 from AGENTS.md.
          <span className="hero-nodata">{v.words}</span>
        )}
      </div>

      <UtilBar v={v} hero />

      {/* `hero-fresh-narrow` duplicates the content of `hero-top`, because in a narrow layout
          the reading goes to the footer and CSS will not move a node between parents. Always
          exactly one is visible. The separator sits in the delta's `::before`, not here: when
          the footer wraps, "·" must go down with what it separates, not orphan at line end. */}
      <div className="hero-foot">
        <span className="hero-fresh-narrow">{v.heroNote}</span>
        <span className="tag tag-neutral tag-num tag-delta">
          {delta(s.deltaPct1h, parseUtc(s.deltaFrom), nowMs)}
        </span>
        <span className="tag tag-neutral tag-num tag-sev">{severityLabel(s.severity)}</span>
        <span className="hero-note">the window you are working in now</span>
      </div>
    </div>
  );
}
