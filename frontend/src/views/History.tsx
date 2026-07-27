import { useMemo, useState } from "react";

import type { StatusResponse } from "../api/types";
import { ErrorBlock, LoadingBlock } from "../components/Blocks";
import { HistoryFacet } from "../components/HistoryFacet";
import { Legend } from "../components/Legend";
import { RANGES } from "../hooks/useHistory";
import { useStatus } from "../hooks/useStatus";
import { dm, hm, parseUtc, tzLabel } from "../lib/time";

/** Wybor serii jest BUDOWANY Z DANYCH, nie z zaszytej listy (zasada 5 z AGENTS.md).
 *
 *  `seriesId` jest inny dla kazdego konta, ale `seriesKey` jest wspolny — wiec kontroler
 *  operuje na kluczach, a kazdy facet odczytuje swoje wlasne id dla wybranego klucza.
 *  Nowy bucket u Anthropic pojawi sie tutaj sam, bez zmiany kodu i bez deployu.
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

/** Kotwica zakresu zaokraglona do minuty — inaczej kazde tyknięcie zegara zmienialoby
 *  klucz zapytania i wykres przeladowywalby sie co sekunde. */
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
          <label>Seria</label>
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
          <label>Zakres</label>
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
          <h4>Nie ma jeszcze żadnej serii</h4>
          <p>Historia pojawi się po pierwszym pomiarze.</p>
        </div>
      ) : (
        q.data.accounts.map((a) => (
          <HistoryFacet
            key={a.uuid}
            account={a}
            series={a.series.find((s) => s.seriesKey === active) ?? null}
            from={from}
            to={to}
          />
        ))
      )}

      <Legend bucket={bucketHint} />
      {q.data.accounts.some((a) => a.series.some((s) => s.freshness === "unknown")) && (
        <div className="state-block hist-caveat" style={{ paddingTop: 0 }}>
          <p>
            Uwaga przy czytaniu: część serii jest w stanie <code>unknown</code>. Wykres pokazuje
            to, co zmierzone — luka na końcu nie znaczy zera. Zerknij na{" "}
            {parseUtc(q.data.serverNow) ? hm(parseUtc(q.data.serverNow)) : "teraz"} w widoku Live.
          </p>
        </div>
      )}
    </>
  );
}
