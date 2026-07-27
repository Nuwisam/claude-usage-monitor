import type { SeriesView } from "../lib/freshness";

/** Tor wypelnienia — cztery stany, cztery rozne rysunki (makieta 2d).
 *
 *  live           pelne wypelnienie akcentem — to pomiar
 *  stale          ten sam ksztalt, wypelnienie przygaszone — odczyt sie starzeje,
 *                 ale wartosc jest nadal prawdziwa, bo zuzycie nie rosnie samo
 *  inferred_reset kontur przerywany + kikut przy zerze — ksztalt pomiaru bez masy
 *  unknown        kontur ze skosem, ZERO wypelnienia, kreska na ostatnim pomiarze
 *
 *  Przy `unknown` brak wypelnienia jest celowy i nienegocjowalny: kazde wypelnienie da
 *  sie przeczytac jako wartosc, a my w tym stanie wartosci nie znamy.
 */
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
