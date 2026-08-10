import { Desktop } from "@phosphor-icons/react";

import type { AccountStatus } from "../api/types";
import { parseUtc, stamp } from "../lib/time";
import { Cascade } from "./Cascade";
import { HeroSession, pickSession } from "./HeroSession";
import { SeriesRow } from "./SeriesRow";

interface Props {
  a: AccountStatus;
  nowMs: number;
}

/** One account's column. The plan (orgType, rateLimitTier, seatTier) sits at every account,
 *  because 40% on Max 20x and 40% on a Team seat are different amounts — which is also why
 *  we nowhere sum or average percentages across accounts. */
export function AccountCard({ a, nowMs }: Props) {
  const hero = pickSession(a.series);
  // `primary` only — the API reports some limits twice (bucket + an entry in limits[]),
  // and `spend` and `extra_usage` are two views of the same credit pool.
  const rest = a.series
    .filter((s) => s !== hero && s.primary)
    .sort((x, y) => x.sortOrder - y.sortOrder || x.seriesKey.localeCompare(y.seriesKey));
  // Looked up in the FULL list, not in `rest`: this series is exactly what the filter above
  // suppresses. Its row was a duplicate, but its flags are the only source of credit state in
  // the UI — so instead of going on screen they go into the explanation at the spend row.
  const eu = a.series.find((s) => s.source === "extra_usage");

  return (
    <section className="account">
      <div className="account-head">
        <div className="account-title">
          <h5>{a.email ?? a.label ?? a.uuid}</h5>
          <span className="account-machine">
            {/* Through `stamp`, because a machine silent for three days used to show a bare
                "11:58:07" here — an hour with no day, to the second. */}
            <Desktop size={13} style={{ verticalAlign: -2 }} /> {a.lastClientHost ?? "—"} ·{" "}
            {stamp(parseUtc(a.lastBatchAt), nowMs, true)}
          </span>
        </div>
        {/* `tag-seat` separately, because the narrow layout hides it (mockup 2b). Through
            @media, not a condition in JS — else the layout would depend on a width known after mount. */}
        <div className="account-tags">
          {[a.orgType, a.rateLimitTier].filter((t): t is string => Boolean(t)).map((t) => (
            <span className="tag tag-neutral tag-mono" key={t}>
              {t}
            </span>
          ))}
          <span className="tag tag-neutral tag-mono tag-seat">
            seatTier: {a.seatTier ?? "—"}
          </span>
        </div>
      </div>

      {hero ? (
        <HeroSession s={hero} nowMs={nowMs} />
      ) : (
        <div className="empty-slot">
          no series has had a value yet — the account reports, but there is nothing to show
        </div>
      )}

      <div className="section-label">
        <span>Cascade</span>
      </div>
      <Cascade rungs={a.cascade} />

      {rest.length > 0 && (
        <>
          <div className="section-label">
            <span>Other windows and limits</span>
          </div>
          <div className="series-list">
            {rest.map((s) => (
              <SeriesRow key={s.seriesKey} s={s} nowMs={nowMs} eu={eu} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}
