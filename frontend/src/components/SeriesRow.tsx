import type { SeriesStatus } from "../api/types";
import { describeSeries, spendNote } from "../lib/freshness";
import { UtilBar } from "./UtilBar";

/** Wiersz jednej serii: etykieta z notatka, tor, liczba.
 *
 *  `isActive` nie przestawia hierarchii widoku (na pierwszym planie zostaje sesja 5 h) —
 *  jest cienka kreska i jednym slowem przy serii, ktora naprawde ogranicza.
 */
export function SeriesRow({ s, showKey }: { s: SeriesStatus; showKey: boolean }) {
  const v = describeSeries(s, spendNote(s));
  const dup = !s.primary;

  // Cztery obszary siatki (title / bar / value / sub) zamiast zagniezdzonych blokow —
  // dzieki temu waski uklad (2b) przestawia je bez zmiany DOM: liczba wskakuje w linie
  // etykiety, tor idzie pod nia, notatka na spod. Patrz grid-template-areas w app.css.
  return (
    <div className="series-row" data-dup={dup}>
      <div className="series-title">
        {s.isActive && <span className="series-active-mark" />}
        <span className="series-label" title={s.label}>
          {s.label}
        </span>
        {s.isActive && <span className="series-binds">wiąże</span>}
        {dup && (
          <span
            className="tag tag-neutral tag-dup"
            title={`ta sama wartość co ${s.duplicateOf}`}
          >
            duplikat
          </span>
        )}
      </div>

      <UtilBar v={v} />

      {v.number !== null ? (
        <span className="series-value series-pct">{v.number}</span>
      ) : (
        <span className="series-value series-nodata">{v.words}</span>
      )}

      <div className="series-sub">
        {showKey && <span className="series-key">{s.seriesKey}</span>}
        <span className="series-note">{v.note}</span>
      </div>
    </div>
  );
}
