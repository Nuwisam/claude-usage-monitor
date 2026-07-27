import type { SeriesStatus } from "../api/types";
import { describeSeries, spendNote } from "../lib/freshness";
import { UtilBar } from "./UtilBar";

/** Wiersz serii. `isActive` nie przestawia hierarchii widoku — jest kreska i slowem.
 *  Cztery obszary siatki zamiast zagniezdzonych blokow, zeby waski uklad przestawil je
 *  bez zmiany DOM (grid-template-areas w app.css). */
export function SeriesRow({ s }: { s: SeriesStatus }) {
  const v = describeSeries(s, spendNote(s));

  return (
    <div className="series-row">
      <div className="series-title">
        {s.isActive && <span className="series-active-mark" />}
        <span className="series-label" title={s.label}>
          {s.label}
        </span>
        {s.isActive && <span className="series-binds">wiąże</span>}
      </div>

      <UtilBar v={v} />

      {v.number !== null ? (
        <span className="series-value series-pct">{v.number}</span>
      ) : (
        <span className="series-value series-nodata">{v.words}</span>
      )}

      <div className="series-sub">
        <span className="series-note">{v.note}</span>
      </div>
    </div>
  );
}
