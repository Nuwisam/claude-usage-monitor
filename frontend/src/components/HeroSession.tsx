import { ClockCountdown } from "@phosphor-icons/react";

import type { SeriesStatus } from "../api/types";
import { delta, severityLabel } from "../lib/format";
import { describeSeries, resetNote } from "../lib/freshness";
import { parseUtc } from "../lib/time";
import { UtilBar } from "./UtilBar";

/** Okno 5 h na pierwszy plan. Wpis z `limits[]` przed bucketem, bo niesie `isActive`
 *  i `severity`. Zaden warunek nie patrzy w tresc klucza — zasada 5 z AGENTS.md. */
export function pickSession(series: SeriesStatus[]): SeriesStatus | null {
  const primary = series.filter((s) => s.primary);
  return (
    primary.find((s) => s.kind === "session") ??
    primary.find((s) => s.bucketKey === "five_hour") ??
    series.find((s) => s.bucketKey === "five_hour") ??
    null
  );
}

/** Hero stoi na sesji 5 h na stale. Gdyby przeskakiwal za `isActive`, ten sam ekran
 *  znaczylby co innego w zaleznosci od pory tygodnia. */
export function HeroSession({ s, nowMs }: { s: SeriesStatus; nowMs: number }) {
  const v = describeSeries(s, nowMs);
  const reset = resetNote(s, nowMs);

  return (
    <div className="card elev-sm hero">
      <div className="hero-top">
        <span className="card-kicker" style={{ color: "var(--color-accent-200)" }}>
          Sesja 5 h
        </span>
        {s.isActive && (
          <span className="hero-binds">
            wiąże<span className="hero-binds-now"> teraz</span>
          </span>
        )}
        <span className="hero-fresh">{v.heroNote}</span>
      </div>

      <div className="hero-main">
        <div className="hero-labels">
          <span className="hero-label">{s.label}</span>
          <span className="hero-reset">
            <ClockCountdown size={14} className="ph" />{" "}
            {reset.lead}
            {/* Bez „o" — przyimek jest juz w `reset.at`, bo zmienia sie razem z dniem. */}
            {reset.at && <span className="hero-reset-at"> · {reset.at}</span>}
          </span>
        </div>
        {v.number !== null ? (
          <div className="hero-value">
            <span className="hero-number">{v.number}</span>
            <span className="hero-unit">%</span>
          </div>
        ) : (
          // Nigdy zero — zasada 4 z AGENTS.md.
          <span className="hero-nodata">{v.words}</span>
        )}
      </div>

      <UtilBar v={v} hero />

      {/* `hero-fresh-narrow` dubluje tresc z `hero-top`, bo w waskim ukladzie odczyt idzie
          do stopki, a CSS nie przeniesie wezla miedzy rodzicami. Widoczny zawsze jeden.
          Separator siedzi w `::before` delty, nie tutaj: gdy stopka sie zawinie, „·" ma isc
          w dol razem z tym, co oddziela, a nie zostac sierota na koncu linii. */}
      <div className="hero-foot">
        <span className="hero-fresh-narrow">{v.heroNote}</span>
        <span className="tag tag-neutral tag-num tag-delta">
          {delta(s.deltaPct1h, parseUtc(s.deltaFrom), nowMs)}
        </span>
        <span className="tag tag-neutral tag-num tag-sev">{severityLabel(s.severity)}</span>
        <span className="hero-note">okno, w którym pracujesz teraz</span>
      </div>
    </div>
  );
}
