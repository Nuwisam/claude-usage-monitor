import { ClockCountdown } from "@phosphor-icons/react";

import type { SeriesStatus } from "../api/types";
import { delta, severityLabel } from "../lib/format";
import { describeSeries } from "../lib/freshness";
import { countdown, hm, parseUtc } from "../lib/time";
import { UtilBar } from "./UtilBar";

/** Wybor serii na pierwszy plan: okno 5 h.
 *
 *  Bierzemy wpis z `limits[]` o `kind === "session"`, bo to on niesie `isActive`
 *  i `severity`; bucket `five_hour` jest jego duplikatem i sluzy jako zapas. Zaden
 *  z warunkow nie patrzy w tresc klucza ani w etykiete — zasada 5 z AGENTS.md.
 */
export function pickSession(series: SeriesStatus[]): SeriesStatus | null {
  const primary = series.filter((s) => s.primary);
  return (
    primary.find((s) => s.kind === "session") ??
    primary.find((s) => s.bucketKey === "five_hour") ??
    series.find((s) => s.bucketKey === "five_hour") ??
    null
  );
}

/** Hero: okno, w ktorym pracujesz TERAZ. Stale na pierwszym planie — gdyby przeskakiwalo
 *  za `isActive`, ten sam ekran znaczylby co innego w zaleznosci od pory tygodnia. */
export function HeroSession({ s, nowMs }: { s: SeriesStatus; nowMs: number }) {
  const v = describeSeries(s);
  const resets = parseUtc(s.resetsAt);

  return (
    <div className="card elev-sm hero">
      <div className="hero-top">
        <span className="card-kicker" style={{ color: "var(--color-accent-200)" }}>
          Sesja 5 h
        </span>
        {s.isActive && <span className="hero-binds">wiąże teraz</span>}
        <span className="hero-fresh">{v.heroNote}</span>
      </div>

      <div className="hero-main">
        <div className="hero-labels">
          <span className="hero-label">{s.label}</span>
          <span className="hero-reset">
            <ClockCountdown size={14} className="ph" />{" "}
            {resets
              ? `reset za ${countdown(resets, nowMs)} · o ${hm(resets)}`
              : "bez resetu"}
          </span>
        </div>
        {v.number !== null ? (
          <div className="hero-value">
            <span className="hero-number">{v.number}</span>
            <span className="hero-unit">%</span>
          </div>
        ) : (
          // Brak liczby jest tu jedyna poprawna odpowiedzia. Zero bylo by klamstwem,
          // na podstawie ktorego odpalisz duze zadanie i trafisz w sciane.
          <span className="hero-nodata">{v.words}</span>
        )}
      </div>

      <UtilBar v={v} hero />

      <div className="hero-foot">
        <span className="tag tag-neutral tag-num">{delta(s.deltaPct1h)}</span>
        <span className="tag tag-neutral tag-num">{severityLabel(s.severity)}</span>
        <span className="hero-note">okno, w którym pracujesz teraz</span>
      </div>
    </div>
  );
}
