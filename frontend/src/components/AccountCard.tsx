import { Desktop } from "@phosphor-icons/react";

import type { AccountStatus } from "../api/types";
import { hms, parseUtc } from "../lib/time";
import { Cascade } from "./Cascade";
import { HeroSession, pickSession } from "./HeroSession";
import { SeriesRow } from "./SeriesRow";

interface Props {
  a: AccountStatus;
  nowMs: number;
  showDuplicates: boolean;
  showKeys: boolean;
}

/** Kolumna jednego konta.
 *
 *  Plan jest widoczny przy KAZDYM koncie (orgType, rateLimitTier, seatTier) i nie jest
 *  ozdoba: 40% na Max 20x i 40% na miejscu Team to rozne ilosci bezwzgledne, wiec bez
 *  etykiety planu te same procenty klamia. Z tego samego powodu nigdzie nie sumujemy
 *  ani nie sredniujemy procentow miedzy kontami.
 */
export function AccountCard({ a, nowMs, showDuplicates, showKeys }: Props) {
  const hero = pickSession(a.series);
  const rest = a.series
    .filter((s) => s !== hero)
    .filter((s) => showDuplicates || s.primary)
    .sort((x, y) => x.sortOrder - y.sortOrder || x.seriesKey.localeCompare(y.seriesKey));

  return (
    <section className="account">
      <div className="account-head">
        <div className="account-title">
          <h5>{a.email ?? a.label ?? a.uuid}</h5>
          <span className="account-machine">
            <Desktop size={13} style={{ verticalAlign: -2 }} /> {a.lastClientHost ?? "—"} ·{" "}
            {hms(parseUtc(a.lastBatchAt))}
          </span>
        </div>
        <div className="account-tags">
          {[a.orgType, a.rateLimitTier, a.seatTier ? `seatTier: ${a.seatTier}` : "seatTier: —"]
            .filter((t): t is string => Boolean(t))
            .map((t) => (
              <span className="tag tag-neutral tag-mono" key={t}>
                {t}
              </span>
            ))}
        </div>
      </div>

      {hero ? (
        <HeroSession s={hero} nowMs={nowMs} />
      ) : (
        <div className="empty-slot">
          żadna seria nie miała jeszcze wartości — konto raportuje, ale nie ma czego pokazać
        </div>
      )}

      <div className="section-label">
        <span>Kaskada</span>
      </div>
      <Cascade rungs={a.cascade} />

      {rest.length > 0 && (
        <>
          <div className="section-label">
            <span>Pozostałe okna i limity</span>
          </div>
          <div className="series-list">
            {rest.map((s) => (
              <SeriesRow key={s.seriesKey} s={s} showKey={showKeys} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}
