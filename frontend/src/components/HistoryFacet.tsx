import type { AccountStatus, SeriesStatus } from "../api/types";
import { statsOf, useHistory } from "../hooks/useHistory";
import { pct } from "../lib/format";
import { describeSeries } from "../lib/freshness";
import { hm } from "../lib/time";
import { ErrorBlock } from "./Blocks";
import { GAP_LABEL, HistoryChart } from "./HistoryChart";

interface Props {
  account: AccountStatus;
  /** Seria TEGO konta odpowiadajaca wybranemu `seriesKey`. null = konto jej nie ma. */
  series: SeriesStatus | null;
  from: Date;
  to: Date;
}

/** Facet per konto, nigdy wspolna os: limit Team zalezy od tier miejsca i jest dzielony
 *  z Claude chat i Cowork, Max ma wlasny — te same 40% to inne ilosci bezwzgledne. */
export function HistoryFacet({ account, series, from, to }: Props) {
  const q = useHistory(account.uuid, series?.seriesId ?? null, from, to);
  const plan = [account.orgType, account.rateLimitTier].filter(Boolean).join(" · ");
  const view = series ? describeSeries(series) : null;

  return (
    <section className="facet">
      <div className="facet-head">
        <h5>{account.email ?? account.uuid}</h5>
        <span className="facet-plan">{plan || "plan nieznany"}</span>
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
          to konto nie ma tej serii — nie ma czego rysować
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
                {GAP_LABEL[g.kind] ?? g.kind} · {hm(new Date(g.from))}–{hm(new Date(g.to))}
              </span>
            ))}
            {q.data && q.data.points.length === 0 && (
              <span className="facet-note">
                brak próbek w tym zakresie — dane przyrastają tylko wtedy, gdy pracujesz
              </span>
            )}
          </div>
        </>
      )}
    </section>
  );
}

function statsLabel(points: Parameters<typeof statsOf>[0] | undefined, loading: boolean) {
  if (loading) return "czytam…";
  if (!points) return "";
  const s = statsOf(points);
  if (!s) return "brak danych w zakresie";
  const n = s.n.toLocaleString("pl-PL");
  return `min ${pct(s.min)} · max ${pct(s.max)} · ostatnia ${pct(s.last) ?? "—"} · n = ${n}`;
}
