import type { SeriesStatus } from "../api/types";
import { creditsFacts, POOL_DEFINITION } from "../lib/credits";
import { describeSeries, resetNote, spendNote } from "../lib/freshness";
import { HelpDot } from "./HelpDot";
import { UtilBar } from "./UtilBar";

interface Props {
  s: SeriesStatus;
  nowMs: number;
  /** This account's suppressed `extra_usage` series — a second view of THE SAME credit pool.
   *  Its row is not drawn (`primary: false`), but its flags are the only answer to the
   *  question "why are the credits not working", so they go under the "?".
   *  Absent on an account that never had credits — and that is a correct state. */
  eu?: SeriesStatus;
}

/** A series row. `isActive` does not rearrange the view hierarchy — it is a dash and a word.
 *  Four grid areas instead of nested blocks, so that a narrow layout can rearrange them
 *  without changing the DOM (grid-template-areas in app.css). */
export function SeriesRow({ s, nowMs, eu }: Props) {
  const v = describeSeries(s, nowMs, spendNote(s));
  const reset = resetNote(s, nowMs);
  // `source` is a contract enum, not a key name (rule 5) — the same test as in `spendNote`.
  const facts = s.source === "spend" ? creditsFacts(s, eu) : null;

  return (
    <div className="series-row">
      <div className="series-title">
        {s.isActive && <span className="series-active-mark" />}
        <span className="series-label" title={s.label}>
          {s.label}
        </span>
        {facts && (
          <HelpDot label="What this pool is and what state the credits are in">
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
        {s.isActive && <span className="series-binds">binds</span>}
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
          {/* No "at" — the preposition is already in `reset.at`, it changes with the day. */}
          {reset.at && <span className="series-reset-at"> · {reset.at}</span>}
        </span>
      </div>
    </div>
  );
}
