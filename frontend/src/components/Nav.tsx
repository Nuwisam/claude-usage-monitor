import { NavLink } from "react-router-dom";

import { CONTRACT_VERSION } from "../api/types";
import type { StreamState } from "../hooks/useLiveStream";
import { STATUS_REFETCH_LIVE_MS, STATUS_REFETCH_MS } from "../hooks/useStatus";
import { ago } from "../lib/time";

interface Props {
  contractVersion: number | undefined;
  updatedAtMs: number | null;
  nowMs: number;
  stalled: boolean;
  stream: StreamState;
}

/** „15 s" / „3 min" — kadencja w naglowku. Lokalne, bo to jedyne miejsce z takim zapisem;
 *  `ago` i `countdown` z lib/time maja inna semantyke („temu", „po resecie"). */
function every(ms: number): string {
  return ms >= 60_000 ? `${Math.round(ms / 60_000)} min` : `${Math.round(ms / 1000)} s`;
}

export function Nav({ contractVersion, updatedAtMs, nowMs, stalled, stream }: Props) {
  const mismatch = contractVersion !== undefined && contractVersion !== CONTRACT_VERSION;
  // Nazwac wprost, skad biora sie dane. „na żywo" znaczy, ze pomiar widac natychmiast;
  // przy zerwanym strumieniu uczciwiej jest pokazac tempo odpytywania niz udawac push.
  const cadence =
    stream === "live"
      ? `na żywo · kontrola co ${every(STATUS_REFETCH_LIVE_MS)}`
      : `co ${every(STATUS_REFETCH_MS)}`;

  return (
    <div className="nav app-nav">
      <span className="nav-brand">Claude Usage</span>
      <NavLink to="/" end>
        Live
      </NavLink>
      <NavLink to="/historia">Historia</NavLink>

      <div className="app-nav-right">
        <span
          className="pill-contract"
          data-mismatch={mismatch}
          title={
            mismatch
              ? `UI zbudowany na kontrakt v${CONTRACT_VERSION}, backend mówi v${contractVersion} — dane mogą być renderowane błędnie`
              : "wersja kontraktu API"
          }
        >
          kontrakt v{contractVersion ?? "?"}
          {mismatch ? ` ≠ v${CONTRACT_VERSION}` : ""}
        </span>
        {/* Caly napis w JEDNYM spanie: `.live-dot` jest flexem z `gap`, wiec rozbicie go
            na rodzenstwo rozpychaloby odstepy miedzy slowami. Wasko zostaje samo
            „3 s temu" (makieta 2b) — reszte chowa @media. */}
        <span className="live-dot" data-stalled={stalled} data-stream={stream}>
          <span>
            {updatedAtMs === null ? (
              "brak odczytu"
            ) : (
              <>
                <span className="live-verb">odświeżono </span>
                {ago(updatedAtMs, nowMs)}
                <span className="live-cadence"> · {cadence}</span>
              </>
            )}
          </span>
        </span>
      </div>
    </div>
  );
}
