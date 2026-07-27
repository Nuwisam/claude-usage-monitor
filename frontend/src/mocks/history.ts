/** Historia z makiety 2c: dwa facety, dwa rodzaje dziur, pieć granic resetu na dobe.
 *
 *  Punkty powstaja z interpolacji miedzy kotwicami — ta sama metoda i te same kotwice,
 *  co w prototypie, zeby wykres dal sie porownac z makieta piksel w piksel.
 */
import type { HistoryQuery } from "../api/client";
import type { HistoryPoint, HistoryResponse } from "../api/types";

const H = 3_600_000;
const STEP = 15 * 60_000;

type Anchor = [hoursAgo: number, value: number];

/** Kotwice liczone w godzinach WSTECZ od `to`, zeby wykres byl zawsze aktualny. */
const MAX_ANCHORS: Anchor[] = [
  [24, 62], [23.1, 66], [23.05, 0], [20, 18], [19.15, 18], [19.1, 0],
  [9.6, 0], [9.15, 14], [9.1, 0], [6.5, 48], [4.15, 48], [4.1, 0],
  [1.2, 29], [0.15, 29], [0, 31],
];
const TEAM_ANCHORS: Anchor[] = [
  [24, 0], [11.1, 0], [9.8, 30], [8.15, 30], [8.1, 0], [7.1, 8],
  [3.15, 8], [3.1, 0], [0.45, 0], [0, 12],
];

const MAX_GAPS = [{ fromH: 1.2, toH: 0.13, kind: "client_silent" as const }];
const TEAM_GAPS = [{ fromH: 7.05, toH: 3.6, kind: "no_samples" as const }];

/** Granice resetu okna 5 h — pieć w dobie, nie o pelnej dobie. */
const MAX_RESETS = [23.1, 19.1, 14.1, 9.1, 4.1];
const TEAM_RESETS = [23.1, 18.1, 13.1, 8.1, 3.1];

function interp(anchors: Anchor[], hoursAgo: number): number {
  const asc = [...anchors].sort((a, b) => b[0] - a[0]); // od najstarszej
  const first = asc[0]!;
  if (hoursAgo >= first[0]) return first[1];
  for (let i = 1; i < asc.length; i++) {
    const prev = asc[i - 1]!;
    const cur = asc[i]!;
    if (hoursAgo >= cur[0]) {
      const span = prev[0] - cur[0] || 1;
      const f = (prev[0] - hoursAgo) / span;
      return prev[1] + (cur[1] - prev[1]) * f;
    }
  }
  return asc[asc.length - 1]![1];
}

export function mockHistory(q: HistoryQuery): HistoryResponse {
  const team = q.account.startsWith("aaaabbbb");
  const anchors = team ? TEAM_ANCHORS : MAX_ANCHORS;
  const gapDefs = team ? TEAM_GAPS : MAX_GAPS;
  const resetsH = team ? TEAM_RESETS : MAX_RESETS;

  const toMs = q.to.getTime();
  const fromMs = q.from.getTime();
  const spanH = (toMs - fromMs) / H;
  const scale = spanH / 24; // kotwice sa opisane na dobie; dluzsze zakresy rozciagamy

  const gaps = gapDefs
    .map((g) => ({
      from: new Date(toMs - g.fromH * scale * H).toISOString(),
      to: new Date(toMs - g.toH * scale * H).toISOString(),
      kind: g.kind,
    }))
    .filter((g) => Date.parse(g.from) >= fromMs);

  const inGap = (ms: number) =>
    gaps.some((g) => ms > Date.parse(g.from) && ms < Date.parse(g.to));

  const points: HistoryPoint[] = [];
  const step = Math.max(STEP, (toMs - fromMs) / 200);
  for (let ms = fromMs; ms <= toMs; ms += step) {
    if (inGap(ms)) continue;
    const v = Math.max(0, interp(anchors, (toMs - ms) / H / scale));
    const rounded = Math.round(v * 10) / 10;
    points.push({
      t: new Date(ms).toISOString(),
      min: rounded,
      max: rounded,
      avg: rounded,
      last: rounded,
      n: 3,
    });
  }

  return {
    bucket: spanH <= 6 ? "raw" : spanH <= 48 ? "5m" : "1h",
    points,
    resets: resetsH
      .map((h) => toMs - h * scale * H)
      .filter((ms) => ms >= fromMs && !inGap(ms))
      .map((ms) => new Date(ms).toISOString()),
    gaps,
  };
}
