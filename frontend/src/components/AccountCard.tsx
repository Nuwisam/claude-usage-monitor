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

/** Kolumna jednego konta. Plan (orgType, rateLimitTier, seatTier) jest przy kazdym koncie,
 *  bo 40% na Max 20x i 40% na miejscu Team to rozne ilosci — dlatego tez nigdzie nie
 *  sumujemy ani nie sredniujemy procentow miedzy kontami. */
export function AccountCard({ a, nowMs }: Props) {
  const hero = pickSession(a.series);
  // Tylko `primary` — API raportuje czesc limitow dwa razy (bucket + wpis w limits[]),
  // a `spend` i `extra_usage` to dwa widoki tej samej puli kredytow.
  const rest = a.series
    .filter((s) => s !== hero && s.primary)
    .sort((x, y) => x.sortOrder - y.sortOrder || x.seriesKey.localeCompare(y.seriesKey));
  // Szukane w PELNEJ liscie, nie w `rest`: ta seria jest wlasnie tym, co filtr wyzej gasi.
  // Jej wiersz byl duplikatem, ale jej flagi sa jedynym zrodlem stanu kredytow w UI —
  // dlatego zamiast na ekran ida do wyjasnienia przy wierszu wydatkow.
  const eu = a.series.find((s) => s.source === "extra_usage");

  return (
    <section className="account">
      <div className="account-head">
        <div className="account-title">
          <h5>{a.email ?? a.label ?? a.uuid}</h5>
          <span className="account-machine">
            {/* Przez `stamp`, bo maszyna milczaca od trzech dni pokazywala tu samo
                „11:58:07" — godzine bez dnia, z sekundowa precyzja. */}
            <Desktop size={13} style={{ verticalAlign: -2 }} /> {a.lastClientHost ?? "—"} ·{" "}
            {stamp(parseUtc(a.lastBatchAt), nowMs, true)}
          </span>
        </div>
        {/* `tag-seat` osobno, bo waski uklad go ukrywa (makieta 2b). Przez @media, nie
            przez warunek w JS — inaczej uklad zalezalby od szerokosci znanej po montowaniu. */}
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
              <SeriesRow key={s.seriesKey} s={s} nowMs={nowMs} eu={eu} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}
