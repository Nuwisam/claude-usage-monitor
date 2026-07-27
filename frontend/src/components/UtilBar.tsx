import type { SeriesView } from "../lib/freshness";

/** Tor wypelnienia — cztery stany, cztery rysunki (makieta 2d):
 *  live pelne wypelnienie · stale przygaszone · inferred_reset kontur z kikutem ·
 *  unknown kontur ze skosem i ZERO wypelnienia (kazde dalo by sie przeczytac jako wartosc). */
export function UtilBar({ v, hero = false }: { v: SeriesView; hero?: boolean }) {
  return (
    <div className={hero ? "bar bar-hero" : "bar"} aria-hidden="true">
      {v.measured && <div className="bar-track" />}
      {v.measured && (
        <div
          className="bar-fill"
          data-fresh={v.dimmed ? "stale" : "live"}
          style={{ width: `${v.barPct}%` }}
        />
      )}
      {v.outline && <div className="bar-outline" data-hatch={v.hatch} />}
      {v.stub && <div className="bar-stub" />}
      {v.ghost && <div className="bar-ghost" style={{ left: `${v.ghostPct}%` }} />}
      {v.full && <div className="bar-full" />}
    </div>
  );
}
