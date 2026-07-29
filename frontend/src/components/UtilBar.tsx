import type { SeriesView } from "../lib/freshness";

/** Tor wypelnienia — DWA rysunki, nie cztery.
 *
 *  Kazdy realny pomiar wyglada tak samo, niezaleznie od wieku: swiezosc niesie etykieta
 *  odczytu pod wierszem, a nie kolor wypelnienia. Kreskowany kontur zostaje wylacznie dla
 *  przypadkow, w ktorych LICZBY NIE MA — wywnioskowanego resetu (kikut przy zerze) i serii,
 *  ktorej nigdy nie zmierzono (skos). Pusty tor czytaloby sie tam jako zero. */
export function UtilBar({ v, hero = false }: { v: SeriesView; hero?: boolean }) {
  return (
    <div className={hero ? "bar bar-hero" : "bar"} aria-hidden="true">
      {v.measured && <div className="bar-track" />}
      {v.measured && <div className="bar-fill" style={{ width: `${v.barPct}%` }} />}
      {v.outline && <div className="bar-outline" data-hatch={v.hatch} />}
      {v.stub && <div className="bar-stub" />}
      {v.full && <div className="bar-full" />}
    </div>
  );
}
