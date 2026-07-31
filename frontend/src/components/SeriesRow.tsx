import type { SeriesStatus } from "../api/types";
import { creditsFacts, POOL_DEFINITION } from "../lib/credits";
import { describeSeries, resetNote, spendNote } from "../lib/freshness";
import { HelpDot } from "./HelpDot";
import { UtilBar } from "./UtilBar";

interface Props {
  s: SeriesStatus;
  nowMs: number;
  /** Zgaszona seria `extra_usage` tego konta — drugi widok TEJ SAMEJ puli kredytow.
   *  Jej wiersz nie jest rysowany (`primary: false`), ale flagi z niej sa jedyna
   *  odpowiedzia na pytanie „dlaczego kredyty nie dzialaja", wiec ida pod „?".
   *  Nieobecna na koncie, ktore kredytow nigdy nie mialo — i to jest poprawny stan. */
  eu?: SeriesStatus;
}

/** Wiersz serii. `isActive` nie przestawia hierarchii widoku — jest kreska i slowem.
 *  Cztery obszary siatki zamiast zagniezdzonych blokow, zeby waski uklad przestawil je
 *  bez zmiany DOM (grid-template-areas w app.css). */
export function SeriesRow({ s, nowMs, eu }: Props) {
  const v = describeSeries(s, nowMs, spendNote(s));
  const reset = resetNote(s, nowMs);
  // `source` to enum kontraktu, nie nazwa klucza (zasada 5) — ten sam test co w `spendNote`.
  const facts = s.source === "spend" ? creditsFacts(s, eu) : null;

  return (
    <div className="series-row">
      <div className="series-title">
        {s.isActive && <span className="series-active-mark" />}
        <span className="series-label" title={s.label}>
          {s.label}
        </span>
        {facts && (
          <HelpDot label="Czym jest ta pula i w jakim stanie są kredyty">
            <span className="help-lead">{POOL_DEFINITION}</span>
            {facts.length > 0 && (
              <span className="help-facts">
                {facts.map((f) => (
                  <span className="help-fact" key={`${f.term}:${f.value}`}>
                    <span className="help-term">{f.term}</span>
                    <span className="help-value">
                      {f.value}
                      {f.code && <code className="help-code">{f.code}</code>}
                    </span>
                  </span>
                ))}
              </span>
            )}
          </HelpDot>
        )}
        {s.isActive && <span className="series-binds">wiąże</span>}
      </div>

      <UtilBar v={v} />

      {v.number !== null ? (
        <span className="series-value series-pct">{v.number}</span>
      ) : (
        <span className="series-value series-nodata">{v.words}</span>
      )}

      <div className="series-sub">
        <span className="series-note">
          {v.note} · <span className="series-reset">{reset.lead}</span>
          {/* Bez „o" — przyimek jest juz w `reset.at`, bo zmienia sie razem z dniem. */}
          {reset.at && <span className="series-reset-at"> · {reset.at}</span>}
        </span>
      </div>
    </div>
  );
}
