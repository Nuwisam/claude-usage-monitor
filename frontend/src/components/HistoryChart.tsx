import type { HistoryGap, HistoryPoint } from "../api/types";
import { hm } from "../lib/time";

/** Wykres z makiety 2c. Geometria jest przepisana z prototypu, nie dobrana na oko:
 *  viewBox 1000x200, os przy y=190, siatka na 148/106/64/22, pole rysowania 168 px.
 *
 *  Ksztalt niesie sciezka SREDNIEJ w koszyku. min/max przychodzi w danych i zostaje
 *  dostepne, ale makieta rysuje jedna linie i tak podpisuje legende.
 */
const W = 1000;
const Y0 = 190;
const Y100 = 22;
const H = Y0 - Y100;

const x = (ms: number, fromMs: number, spanMs: number) =>
  +(((ms - fromMs) / spanMs) * W).toFixed(2);
const y = (v: number) => +(Y0 - (v / 100) * H).toFixed(2);

interface Props {
  points: HistoryPoint[];
  resets: string[];
  gaps: HistoryGap[];
  from: Date;
  to: Date;
  /** Unikalny prefiks id — dwa facety na jednej stronie nie moga dzielic <pattern>. */
  uid: string;
}

export function HistoryChart({ points, resets, gaps, from, to, uid }: Props) {
  const fromMs = from.getTime();
  const spanMs = Math.max(1, to.getTime() - fromMs);
  const gapRanges = gaps.map((g) => [Date.parse(g.from), Date.parse(g.to)] as const);

  /** Sciezka LAMIE SIE na dziurach — osobne `M` na kazdy ciagly odcinek. Linia
   *  przeciagnieta przez dziure zmyslalaby dane, ktorych nie mamy. */
  const segments: string[] = [];
  let current: string[] = [];
  for (const p of points) {
    const ms = Date.parse(p.t);
    const inGap = gapRanges.some(([a, b]) => ms > a && ms < b);
    if (p.avg === null || inGap) {
      if (current.length > 1) segments.push(`M${current.join(" L")}`);
      current = [];
      continue;
    }
    current.push(`${x(ms, fromMs, spanMs)} ${y(p.avg)}`);
  }
  if (current.length > 1) segments.push(`M${current.join(" L")}`);

  const ticks = xTicks(fromMs, spanMs);

  return (
    <div className="chart-grid">
      <div className="chart-y">
        {[100, 75, 50, 25, 0].map((v) => (
          <span key={v}>{v}</span>
        ))}
      </div>
      <div className="chart-body">
        <svg
          className="chart-svg"
          viewBox={`0 0 ${W} 200`}
          preserveAspectRatio="none"
          role="img"
          aria-label="przebieg wykorzystania limitu w czasie"
        >
          <defs>
            {/* Dwa wzory, bo dziury znacza dwie rozne rzeczy: skos = klient milczal,
                kropki = klient dzialal, ale dla tej serii nie bylo probek (awaria). */}
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
              // preserveAspectRatio="none" rozciaga tez grubosc kreski — bez tego linia
              // bylaby grubsza w poziomie niz w pionie.
              vectorEffect="non-scaling-stroke"
            />
          ))}
        </svg>

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

/** Podzialka co rowna godzine/dobe, zaleznie od dlugosci zakresu — nigdy wiecej niz 8. */
function xTicks(fromMs: number, spanMs: number) {
  const hours = spanMs / 3_600_000;
  const stepH = hours <= 8 ? 1 : hours <= 30 ? 3 : hours <= 200 ? 24 : 24 * 5;
  const step = stepH * 3_600_000;
  const first = Math.ceil(fromMs / step) * step;
  const out: { ms: number; pct: number; label: string }[] = [];
  for (let ms = first; ms <= fromMs + spanMs; ms += step) {
    out.push({
      ms,
      pct: +(((ms - fromMs) / spanMs) * 100).toFixed(2),
      label: stepH >= 24 ? dayLabel(ms) : hm(new Date(ms)),
    });
  }
  return out;
}

function dayLabel(ms: number): string {
  const d = new Date(ms);
  return `${String(d.getUTCDate()).padStart(2, "0")}.${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}
