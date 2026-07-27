/** Legenda wykresu. Zdanie na koncu nie jest ozdoba — pokrycie tego narzedzia jest
 *  z natury dziurawe, wiec dziura jest informacja, a nie usterka rysowania. */
export function Legend({ bucket }: { bucket?: string }) {
  return (
    <div className="legend">
      <span className="legend-item">
        <span className="sw-line" />
        średnia w koszyku{bucket ? ` ${bucket}` : ""}
      </span>
      <span className="legend-item">
        <span className="sw-reset" />
        granica resetu
      </span>
      <span className="legend-item">
        <span className="sw-silent" />
        cisza klienta
      </span>
      <span className="legend-item">
        <span className="sw-nosamples" />
        brak próbek serii
      </span>
      <span className="legend-closing">Brak danych jest informacją, nie usterką wykresu.</span>
    </div>
  );
}
