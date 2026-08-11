import { useQuery } from "@tanstack/react-query";

import { fetchHistory } from "../api/client";
import type { HistoryPoint } from "../api/types";

export interface RangeOption {
  id: string;
  label: string;
  hours: number;
}

/** Ranges from mockup 2c. `bucket=auto` in the backend: <=6 h raw, <=48 h 5m, above that 1h. */
export const RANGES: RangeOption[] = [
  { id: "6h", label: "6 h", hours: 6 },
  { id: "24h", label: "24 h", hours: 24 },
  { id: "7d", label: "7 d", hours: 24 * 7 },
  { id: "30d", label: "30 d", hours: 24 * 30 },
];

export function useHistory(
  account: string,
  seriesId: number | null,
  from: Date,
  to: Date,
) {
  return useQuery({
    // `to` in the key is rounded by the caller — otherwise every tick of the clock
    // would create a new key and reload the chart.
    queryKey: ["history", account, seriesId, from.toISOString(), to.toISOString()],
    queryFn: () => fetchHistory({ account, seriesId: seriesId as number, from, to }),
    enabled: seriesId !== null,
    staleTime: 60_000,
    retry: 1,
  });
}

export interface Stats {
  min: number;
  max: number;
  last: number | null;
  n: number;
}

/** min/max/last/n for the facet caption — computed here, because the backend already returns
 *  aggregates per bucket and there is no point asking it a second time for the same thing. */
export function statsOf(points: HistoryPoint[]): Stats | null {
  const withValue = points.filter((p) => p.avg !== null);
  if (withValue.length === 0) return null;
  let min = Infinity;
  let max = -Infinity;
  let n = 0;
  for (const p of withValue) {
    if (p.min !== null) min = Math.min(min, p.min);
    if (p.max !== null) max = Math.max(max, p.max);
    n += p.n;
  }
  const tail = withValue[withValue.length - 1];
  return {
    min: Number.isFinite(min) ? min : 0,
    max: Number.isFinite(max) ? max : 0,
    last: tail?.last ?? null,
    n,
  };
}
