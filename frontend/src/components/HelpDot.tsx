import { useEffect, useId, useRef, useState } from "react";

/** A "?" dot with an explanation. The first interactive element in the account card — until now
 *  it was purely presentational, so this file is also a precedent and that is why it is so small.
 *
 *  WHY A BUTTON AND NOT `title`: the `title` attribute is used in the repo for shorthands (a
 *  series label, the current-rung marker) and there it is enough, because it repeats text that
 *  sits right next to it.
 *  Here the content is the ONLY carrier of this information, and `title` opens neither from the
 *  keyboard nor by touch — so on a phone and when navigating with Tab, this information is
 *  simply unreachable.
 *  `<button>` with `aria-expanded` gives both entry points without a single dependency; the repo
 *  has no popover library and we are not adding one for a single use.
 */
export function HelpDot({ label, children }: { label: string; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const box = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    // `pointerdown`, not `click`: with `click` the panel closed only once the button was
    // released, so selecting text INSIDE the panel ended with the panel closing.
    const away = (e: PointerEvent) => {
      if (!box.current?.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      // Focus returns to the dot — otherwise after Escape it would land on `body` and Tab
      // would start from the top of the page.
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
      {/* The panel is in the DOM only when open: a screen reader skips a collapsed `hidden` one
          anyway, and the content reaches the series siblings — no point computing it in advance. */}
      {open && (
        <span className="series-help-panel" id={id} role="note">
          {children}
        </span>
      )}
    </span>
  );
}
