import { WarningCircle } from "@phosphor-icons/react";

/** Baner z `warnings[]`. Dzis backend NIE generuje zadnego — ostrzezenie o serii w stanie
 *  `unknown` zniklo razem z samym pojeciem w UI, bo etykieta wieku odczytu mowi to samo
 *  liczba minut, zamiast nazwa stanu.
 *
 *  Komponent zostaje, bo `warnings[]` zostaje w kontrakcie: pole jest miejscem na fakty
 *  ponad kontami, ktorych karta konta nie ma jak pokazac. Puste `warnings[]` to poprawny
 *  stan, nie brak implementacji. */
export function Warnings({ items }: { items: string[] }) {
  if (items.length === 0) return null;
  return (
    <>
      {items.map((text) => (
        <div className="banner" key={text} role="status">
          <WarningCircle size={16} className="banner-icon" />
          <div className="banner-lines">
            <span className="banner-text">{text}</span>
            <span className="banner-source">
              z pola <code>warnings[]</code> odpowiedzi /status
            </span>
          </div>
        </div>
      ))}
    </>
  );
}
