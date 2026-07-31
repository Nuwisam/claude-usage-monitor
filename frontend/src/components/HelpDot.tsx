import { useEffect, useId, useRef, useState } from "react";

/** Kropka „?" z wyjasnieniem. Pierwszy element interaktywny w karcie konta — dotad byla
 *  czysto prezentacyjna, wiec ten plik jest tez precedensem i dlatego jest tak maly.
 *
 *  DLACZEGO PRZYCISK, A NIE `title`: atrybut `title` jest w repo uzywany do skrotow
 *  (etykieta serii, znacznik biezacego szczebla) i tam wystarcza, bo powtarza tekst, ktory
 *  obok stoi.
 *  Tutaj tresc jest JEDYNYM nosnikiem tej informacji, a `title` nie otwiera sie ani
 *  z klawiatury, ani dotykiem — czyli na telefonie i pod Tabem wiedza po prostu nie
 *  istnieje. `<button>` z `aria-expanded` daje oba wejscia bez ani jednej zaleznosci;
 *  biblioteki popoverow repo nie ma i dla jednego uzycia jej nie dodajemy.
 */
export function HelpDot({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const box = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    // `pointerdown`, nie `click`: przy `click` panel zamykal sie dopiero po puszczeniu
    // przycisku, wiec zaznaczanie tekstu W SRODKU panelu konczylo sie jego zamknieciem.
    const away = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      // Fokus wraca na kropke — inaczej po Escape ladowalby na `body` i Tab startowalby
      // od poczatku strony.
      box.current?.querySelector("button")?.focus();
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("keydown", key);
    };
  }, [open]);

  return (
    <span className="series-help" ref={box}>
      <button
        type="button"
        className="series-help-dot"
        aria-label={label}
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((v) => !v)}
      >
        ?
      </button>
      {/* Panel jest w DOM tylko gdy otwarty: zwiniety `hidden` czytnik ekranu i tak pomija,
          a tresc siega do rodzenstwa serii i nie ma po co liczyc jej na zapas. */}
      {open && (
        <span className="series-help-panel" id={id} role="note">
          {children}
        </span>
      )}
    </span>
  );
}
