import type { AccountStatus, SeriesStatus } from "../api/types";
import { statsOf, useHistory } from "../hooks/useHistory";
import { pct } from "../lib/format";
import { describeSeries } from "../lib/freshness";
import { stampRange } from "../lib/time";
import { ErrorBlock } from "./Blocks";
import { GAP_LABEL, HistoryChart } from "./HistoryChart";

interface Props {
  account: AccountStatus;
  /** THIS account's series matching the selected `seriesKey`. null = the account has none. */
  series: SeriesStatus | null;
  from: Date;
  to: Date;
  /** The minute anchor from History — not a clock ticking every second, because the view
   *  would reload on every tick. The facet reads only the number from it, so a minute is enough. */
  nowMs: number;
}

/** A facet per account, never a shared axis: the Team limit depends on the seat tier and is
 *  shared with Claude chat and Cowork, Max has its own — the same 40% is a different amount. */
export function HistoryFacet({ account, series, from, to, nowMs }: Props) {
  const q = useHistory(account.uuid, series?.seriesId ?? null, from, to);
  const plan = [account.orgType, account.rateLimitTier].filter(Boolean).join(" · ");
  const view = series ? describeSeries(series, nowMs) : null;

  return (
    <section className="facet">
      <div className="facet-head">
        <h5>{account.email ?? account.uuid}</h5>
        <span className="facet-plan">{plan || "plan unknown"}</span>
        <span className="facet-stats">{statsLabel(q.data?.points, q.isLoading)}</span>
        {view?.number !== null && view?.number !== undefined ? (
          <span className="facet-now">{view.number}%</span>
        ) : (
          <span className="facet-now" data-nodata="true">
            {view?.words ?? "—"}
          </span>
        )}
      </div>

      {series === null ? (
        <div className="empty-slot" style={{ marginTop: "var(--space-4)" }}>
          this account does not have this series — there is nothing to draw
        </div>
      ) : q.error ? (
        <ErrorBlock error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <>
          <HistoryChart
            points={q.data?.points ?? []}
            resets={q.data?.resets ?? []}
            gaps={q.data?.gaps ?? []}
            from={from}
            to={to}
            uid={`f-${account.uuid.slice(0, 8)}`}
          />
          <div className="facet-foot">
            {(q.data?.gaps ?? []).map((g) => (
              <span className="facet-gap" key={`${g.kind}-${g.from}`}>
                {GAP_LABEL[g.kind] ?? g.kind} · {stampRange(new Date(g.from), new Date(g.to))}
              </span>
            ))}
            {q.data && q.data.points.length === 0 && (
              <span className="facet-note">
                no samples in this range — the data only accumulates while you are working
              </span>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function statsLabel(points: Parameters<typeof statsOf>[0] | undefined, loading: boolean) {
  if (loading) return "reading…";
  if (!points) return "";
  const s = statsOf(points);
  if (!s) return "no data in the range";
  const n = s.n.toLocaleString("en-US");
  return `min ${pct(s.min)} · max ${pct(s.max)} · last ${pct(s.last) ?? "—"} · n = ${n}`;
}
