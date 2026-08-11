/** History from mockup 2c: two facets, two kinds of gap, five reset boundaries per day.
 *
 *  The points come from interpolation between anchors — the same method and the same anchors
 *  as in the prototype, so that the chart can be compared with the mockup pixel for pixel.
 */
import type { HistoryQuery } from "../api/client";
import type { HistoryPoint, HistoryResponse } from "../api/types";

const H = 3_600_000;
const STEP = 15 * 60_000;

type Anchor = [hoursAgo: number, value: number];

/** Anchors counted in hours BACK from `to`, so that the chart is always current. */
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

/** Reset boundaries of the 5 h window — five within a day, not at a whole-day mark. */
const MAX_RESETS = [23.1, 19.1, 14.1, 9.1, 4.1];
const TEAM_RESETS = [23.1, 18.1, 13.1, 8.1, 3.1];

function interp(anchors: Anchor[], hoursAgo: number): number {
  const asc = [...anchors].sort((a, b) => b[0] - a[0]); // from the oldest
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
  const scale = spanH / 24; // anchors are described over one day; longer ranges we stretch

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
