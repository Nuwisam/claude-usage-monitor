import { type CSSProperties, useRef, useState } from "react";

import type { HistoryGap, HistoryPoint } from "../api/types";
import { pct } from "../lib/format";
// `dm`/`hm` stay without `stamp()`: the axis and the point tooltip read an instant against the
// WIDTH of the range (`withDay`), not against "now" — `stamp()` would glue a date onto every point.
import { dm, hm, stampRange } from "../lib/time";

/** The chart from mockup 2c. Geometry copied from the prototype, not eyeballed:
 *  viewBox 1000x200, axis y=190, grid 148/106/64/22, drawing field 168 px. */
const W = 1000;
const Y0 = 190;
const Y100 = 22;
const H = Y0 - Y100;
const DOT_R = 2.2;

const x = (ms: number, fromMs: number, spanMs: number) =>
  +(((ms - fromMs) / spanMs) * W).toFixed(2);
const y = (v: number) => +(Y0 - (v / 100) * H).toFixed(2);

export const GAP_LABEL: Record<string, string> = {
  client_silent: "client silent",
  no_samples: "no samples for this series",
};

interface Props {
  points: HistoryPoint[];
  resets: string[];
  gaps: HistoryGap[];
  from: Date;
  to: Date;
  /** Unique id prefix — two facets on one page must not share a <pattern>. */
  uid: string;
}

interface Hover {
  xPct: number;
  /** Dot position in % of the chart height. null = no measurement, so there is no dot either. */
  yPct: number | null;
  head: string;
  body: string;
  note: string | null;
  /** The tooltip over a gap has a different tone — it is not a reading. */
  muted: boolean;
}

export function HistoryChart({ points, resets, gaps, from, to, uid }: Props) {
  const fromMs = from.getTime();
  const spanMs = Math.max(1, to.getTime() - fromMs);
  const gapRanges = gaps.map((g) => [Date.parse(g.from), Date.parse(g.to)] as const);
  const withDay = spanMs > 12 * 3_600_000;

  const plotRef = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<Hover | null>(null);

  /** The order of the rules carries meaning: first the snap to a real sample, then the gap.
   *  The other way round, a tooltip over a gap would substitute the nearest measurement. */
  function hoverAt(clientX: number): Hover | null {
    const rect = plotRef.current?.getBoundingClientRect();
    if (!rect || rect.width === 0) return null;
    const frac = (clientX - rect.left) / rect.width;
    if (frac < 0 || frac > 1) return null;
    const ms = fromMs + frac * spanMs;

    let best: HistoryPoint | null = null;
    let bestDist = Infinity;
    for (const p of points) {
      const d = Math.abs(Date.parse(p.t) - ms);
      if (d < bestDist) {
        bestDist = d;
        best = p;
      }
    }
    // 10 px in time units — the snap has to be felt, but must not reach across half the
    // chart when the data is sparse.
    const snapMs = (spanMs / rect.width) * 10;
    const gap = gaps.find((g) => ms >= Date.parse(g.from) && ms <= Date.parse(g.to));

    if (gap && (best === null || bestDist > snapMs)) {
      return {
        xPct: frac * 100,
        yPct: null,
        head: stampRange(new Date(gap.from), new Date(gap.to)),
        body: GAP_LABEL[gap.kind] ?? gap.kind,
        note: null,
        muted: true,
      };
    }
    if (best === null) return null;

    const t = new Date(best.t);
    const value = pct(best.avg);
    return {
      xPct: x(Date.parse(best.t), fromMs, spanMs) / 10,
      yPct: best.avg === null ? null : (y(best.avg) / 200) * 100,
      head: withDay ? `${dm(t)} ${hm(t)}` : hm(t),
      body: value === null ? "no measurement" : `${value}%`,
      note: noteFor(best),
      muted: value === null,
    };
  }

  /** The path breaks at the gaps — a separate `M` for every continuous run.
   *  The condition looks at the SEGMENT between points, not at points inside the gap: a gap
   *  is precisely a period without samples, so looking for points in it never finds any. */
  const runs: { x: number; y: number }[][] = [];
  let run: { x: number; y: number }[] = [];
  let prevMs: number | null = null;
  const cut = () => {
    if (run.length) runs.push(run);
    run = [];
  };
  for (const p of points) {
    const ms = Date.parse(p.t);
    if (p.avg === null) {
      cut();
      prevMs = ms;
      continue;
    }
    if (prevMs !== null && gapRanges.some(([a, b]) => prevMs! < b && ms > a)) cut();
    run.push({ x: x(ms, fromMs, spanMs), y: y(p.avg) });
    prevMs = ms;
  }
  cut();

  const segments = runs
    .filter((r) => r.length > 1)
    .map((r) => `M${r.map((q) => `${q.x} ${q.y}`).join(" L")}`);
  // A single measurement makes no segment, so it gets a marker of its own.
  const dots = runs.filter((r) => r.length === 1).map((r) => r[0]!);

  const ticks = xTicks(fromMs, spanMs);

  return (
    <div className="chart-grid">
      <div className="chart-y">
        {[100, 75, 50, 25, 0].map((v) => (
          <span key={v}>{v}</span>
        ))}
      </div>
      <div className="chart-body">
        <div
          className="chart-plot"
          ref={plotRef}
          onPointerMove={(e) => setHover(hoverAt(e.clientX))}
          onPointerLeave={() => setHover(null)}
        >
          <svg
            className="chart-svg"
            viewBox={`0 0 ${W} 200`}
            preserveAspectRatio="none"
            role="img"
            aria-label="limit utilization over time"
          >
            <defs>
              {/* diagonal = the client was silent; dots = the client ran, but no samples for the series */}
              <pattern
                id={`${uid}-silent`}
                width="8"
                height="8"
                patternTransform="rotate(45)"
                patternUnits="userSpaceOnUse"
              >
                <line x1="0" y1="0" x2="0" y2="8" stroke="var(--color-text)" strokeOpacity="0.18" strokeWidth="3" />
              </pattern>
              <pattern id={`${uid}-nosamples`} width="7" height="7" patternUnits="userSpaceOnUse">
                <circle cx="1.4" cy="1.4" r="1.1" fill="var(--color-text)" fillOpacity="0.22" />
              </pattern>
            </defs>

            <line x1="0" y1={Y0} x2={W} y2={Y0} stroke="var(--color-text)" strokeOpacity="0.24" />
            {[148, 106, 64, 22].map((gy) => (
              <line key={gy} x1="0" y1={gy} x2={W} y2={gy} stroke="var(--color-text)" strokeOpacity="0.09" />
            ))}

            {gaps.map((g) => {
              const gx = x(Date.parse(g.from), fromMs, spanMs);
              const gw = Math.max(0, x(Date.parse(g.to), fromMs, spanMs) - gx);
              return (
                <rect
                  key={`${g.kind}-${g.from}`}
                  x={gx}
                  y={Y100}
                  width={gw}
                  height={H}
                  fill={`url(#${uid}-${g.kind === "client_silent" ? "silent" : "nosamples"})`}
                />
              );
            })}

            {resets.map((r) => (
              <line
                key={r}
                x1={x(Date.parse(r), fromMs, spanMs)}
                y1="14"
                x2={x(Date.parse(r), fromMs, spanMs)}
                y2={Y0}
                stroke="var(--color-accent-300)"
                strokeOpacity="0.55"
                strokeDasharray="2 4"
              />
            ))}

            {segments.map((d) => (
              <path
                key={d.slice(0, 24)}
                d={d}
                fill="none"
                stroke="var(--color-accent-300)"
                strokeWidth="1.7"
                strokeLinejoin="round"
                // preserveAspectRatio="none" would stretch the stroke width too
                vectorEffect="non-scaling-stroke"
              />
            ))}

          </svg>

          {/* Outside the SVG, because `preserveAspectRatio="none"` squeezes the X axis and distorts a circle. */}
          {dots.map((d) => (
            <span
              key={`${d.x}-${d.y}`}
              className="chart-point"
              style={{
                left: `clamp(${DOT_R}px, ${d.x / 10}%, calc(100% - ${DOT_R}px))`,
                top: `${(d.y / 200) * 100}%`,
              }}
            />
          ))}

          {hover && (
            <>
              <div className="chart-cursor" style={{ left: `${hover.xPct}%` }} />
              {hover.yPct !== null && (
                <div
                  className="chart-dot"
                  style={{ left: `${hover.xPct}%`, top: `${hover.yPct}%` }}
                />
              )}
              <div
                className="chart-tip"
                data-muted={hover.muted || undefined}
                style={tipStyle(hover)}
              >
                <b>{hover.body}</b>
                <span>{hover.head}</span>
                {hover.note && <i>{hover.note}</i>}
              </div>
            </>
          )}
        </div>

        <div className="chart-x">
          {ticks.map((t) => (
            <span key={t.ms} style={{ left: `${t.pct}%` }}>
              {t.label}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

/** A tick every whole hour/day, never more than 8 ticks.
 *
 *  We align through `Date` to LOCAL boundaries, not by arithmetic on `ms`: that one lands on
 *  whole UTC hours, so in a half-hour zone every tick would read ":30".
 *  It also handles the DST change — a day with a change has 23 or 25 hours. */
function xTicks(fromMs: number, spanMs: number) {
  const hours = spanMs / 3_600_000;
  const stepH = hours <= 8 ? 1 : hours <= 30 ? 3 : hours <= 200 ? 24 : 24 * 5;

  const cur = new Date(fromMs);
  cur.setMinutes(0, 0, 0);
  if (stepH >= 24) cur.setHours(0);
  else while (cur.getHours() % stepH !== 0) cur.setHours(cur.getHours() + 1);
  while (cur.getTime() < fromMs) cur.setHours(cur.getHours() + stepH);

  const end = fromMs + spanMs;
  const out: { ms: number; pct: number; label: string }[] = [];
  while (cur.getTime() <= end) {
    const ms = cur.getTime();
    out.push({
      ms,
      pct: +(((ms - fromMs) / spanMs) * 100).toFixed(2),
      label: stepH >= 24 ? dm(new Date(ms)) : hm(new Date(ms)),
    });
    cur.setHours(cur.getHours() + stepH);
  }
  return out;
}

/** The average blurs peaks, so min-max and n go next to it. Downsampling must be visible. */
function noteFor(p: HistoryPoint): string | null {
  if (p.n <= 1) return null;
  const lo = pct(p.min);
  const hi = pct(p.max);
  if (lo !== null && hi !== null && p.min !== p.max) return `${lo}–${hi}% · n = ${p.n}`;
  return `n = ${p.n}`;
}

/** The tooltip must not run off the chart or cover the dot it describes. */
function tipStyle(h: Hover): CSSProperties {
  const tx = h.xPct < 16 ? "0" : h.xPct > 84 ? "-100%" : "-50%";
  const noDot = h.yPct === null;
  // yPct grows downwards, so a small value = a high point = the tooltip must go BELOW the dot
  const ty = noDot ? "0" : h.yPct! > 34 ? "calc(-100% - 12px)" : "12px";
  return {
    left: `${h.xPct}%`,
    top: `${noDot ? 6 : h.yPct}%`,
    transform: `translate(${tx}, ${ty})`,
  };
}
