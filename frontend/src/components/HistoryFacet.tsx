import type { AccountStatus, SeriesStatus } from "../api/types";
import { statsOf, useHistory } from "../hooks/useHistory";
import { pct } from "../lib/format";
import { describeSeries } from "../lib/freshness";
import { hm } from "../lib/time";
import { ErrorBlock } from "./Blocks";
import { HistoryChart } from "./HistoryChart";

interface Props {
  account: AccountStatus;
  /** Seria TEGO konta odpowiadajaca wybranemu `seriesKey`. null = konto jej nie ma. */
  series: SeriesStatus | null;
  from: Date;
  to: Date;
}

const KIND_LABEL: Record<string, string> = {
  client_silent: "cisza klienta",
  no_samples: "brak próbek dla serii",
};

/** Facet per konto — nigdy jedna os dla obu kont bez etykiet planu.
 *
 *  Limit Team zalezy od tier miejsca i jest dzielony z Claude chat oraz Cowork, a Max ma
 *  wlasny tier. Te same 40% to inne ilosci bezwzgledne, wiec nakladanie ich na jedna os
 *  bez planu w legendzie produkowaloby porownanie, ktore nic nie znaczy.
 */
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
                {KIND_LABEL[g.kind] ?? g.kind} · {hm(new Date(g.from))}–{hm(new Date(g.to))}
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
  return `min ${pct(s.min)} · max ${pct(s.max)} · ostatnia ${pct(s.last) ?? "—"} · n = ${s.n}`;
}
