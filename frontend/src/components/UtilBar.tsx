import type { SeriesView } from "../lib/freshness";

/** The fill track — TWO drawings, not four.
 *
 *  Every real measurement looks the same, regardless of age: freshness is carried by the
 *  reading label under the row, not by the color of the fill. The dashed outline stays solely
 *  for the cases where THERE IS NO NUMBER — an inferred reset (a stub at zero) and a series
 *  that has never been measured (hatching). An empty track would read there as zero. */
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
