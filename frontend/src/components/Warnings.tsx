import { WarningCircle } from "@phosphor-icons/react";

/** Baner z `warnings[]`. Dzis backend generuje je z jednego powodu: seria w stanie
 *  `unknown`, czyli dzialajacy klient bez probek. To awaria zbierania danych i ma byc
 *  widoczna od razu, a nie dopiero po wejsciu w szczegoly konta. */
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
