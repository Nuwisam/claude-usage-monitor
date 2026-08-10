/** The chart legend. The closing sentence is not decoration — this tool's coverage is
 *  full of holes by nature, so a gap is information, not a defect in the drawing. */
export function Legend({ bucket }: { bucket?: string }) {
  return (
    <div className="legend">
      <span className="legend-item">
        <span className="sw-line" />
        average in bucket{bucket ? ` ${bucket}` : ""}
      </span>
      <span className="legend-item">
        <span className="sw-reset" />
        reset boundary
      </span>
      <span className="legend-item">
        <span className="sw-silent" />
        client silent
      </span>
      <span className="legend-item">
        <span className="sw-nosamples" />
        no samples for this series
      </span>
      <span className="legend-closing">Missing data is information, not a defect in the chart.</span>
    </div>
  );
}
