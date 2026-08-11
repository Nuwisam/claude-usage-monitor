import { useMemo, useState } from "react";

import type { StatusResponse } from "../api/types";
import { ErrorBlock, LoadingBlock } from "../components/Blocks";
import { HistoryFacet } from "../components/HistoryFacet";
import { Legend } from "../components/Legend";
import { RANGES } from "../hooks/useHistory";
import { useStatus } from "../hooks/useStatus";
import { dm, hm, tzLabel } from "../lib/time";

/** The choice of series is BUILT FROM THE DATA, not from a hardcoded list (rule 5 of AGENTS.md).
 *
 *  `seriesId` differs for every account, but `seriesKey` is shared — so the controller works
 *  on keys, and every facet reads its own id for the selected key.
 *  A new bucket at Anthropic will show up here by itself, with no code change and no deploy.
 */
function seriesOptions(status: StatusResponse) {
  const seen = new Map<string, { key: string; label: string; sort: number }>();
  for (const a of status.accounts) {
    for (const s of a.series) {
      if (!s.primary || seen.has(s.seriesKey)) continue;
      seen.set(s.seriesKey, { key: s.seriesKey, label: s.label, sort: s.sortOrder });
    }
  }
  return [...seen.values()].sort((x, y) => x.sort - y.sort || x.label.localeCompare(y.label));
}

/** The range anchor rounded to the minute — otherwise every tick of the clock would change
 *  the query key and the chart would reload every second. */
function useMinuteAnchor(): Date {
  const [, setTick] = useState(0);
  useMemo(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), 60_000);
    return () => window.clearInterval(id);
  }, []);
  const ms = Math.floor(Date.now() / 60_000) * 60_000;
  return useMemo(() => new Date(ms), [ms]);
}

export function History() {
  const q = useStatus();
  const to = useMinuteAnchor();
  const [rangeId, setRangeId] = useState("24h");
  const [seriesKey, setSeriesKey] = useState<string | null>(null);

  const options = useMemo(() => (q.data ? seriesOptions(q.data) : []), [q.data]);
  const range = RANGES.find((r) => r.id === rangeId) ?? RANGES[1]!;
  const from = useMemo(
    () => new Date(to.getTime() - range.hours * 3_600_000),
    [to, range.hours],
  );

  if (q.isLoading) return <LoadingBlock />;
  if (q.error || !q.data) return <ErrorBlock error={q.error} onRetry={() => q.refetch()} />;

  const active = seriesKey ?? options[0]?.key ?? null;
  const bucketHint = range.hours <= 6 ? "raw" : range.hours <= 48 ? "5m" : "1h";

  return (
    <>
      <div className="hist-controls">
        <div className="field">
          <label>Series</label>
          <div className="seg">
            {options.map((o) => (
              <label className="seg-opt" key={o.key} title={o.key}>
                <input
                  type="radio"
                  name="hist-series"
                  checked={active === o.key}
                  onChange={() => setSeriesKey(o.key)}
                />
                <span>{o.label}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="field">
          <label>Range</label>
          <div className="seg">
            {RANGES.map((r) => (
              <label className="seg-opt" key={r.id}>
                <input
                  type="radio"
                  name="hist-range"
                  checked={rangeId === r.id}
                  onChange={() => setRangeId(r.id)}
                />
                <span>{r.label}</span>
              </label>
            ))}
          </div>
        </div>

        <div className="hist-meta">
          <span className="hist-meta-mono">bucket=auto → {bucketHint}</span>
          <span className="hist-meta-range">
            {dm(from)} {hm(from)} → {dm(to)} {hm(to)} {tzLabel(to)}
          </span>
        </div>
      </div>

      {active === null ? (
        <div className="state-block">
          <h4>No series yet</h4>
          <p>History appears after the first measurement.</p>
        </div>
      ) : (
        q.data.accounts.map((a) => (
          <HistoryFacet
            key={a.uuid}
            account={a}
            series={a.series.find((s) => s.seriesKey === active) ?? null}
            from={from}
            to={to}
            nowMs={to.getTime()}
          />
        ))
      )}

      {/* The note block about the `unknown` state was removed together with the notion itself
          in the UI. Its true content — that a gap does not mean zero — lives in `Legend`. */}
      <Legend bucket={bucketHint} />
    </>
  );
}
