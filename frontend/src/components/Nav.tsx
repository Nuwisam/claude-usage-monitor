import { NavLink } from "react-router-dom";

import { CONTRACT_VERSION } from "../api/types";
import { STATUS_REFETCH_MS } from "../hooks/useStatus";
import { ago } from "../lib/time";

interface Props {
  contractVersion: number | undefined;
  updatedAtMs: number | null;
  nowMs: number;
  stalled: boolean;
}

export function Nav({ contractVersion, updatedAtMs, nowMs, stalled }: Props) {
  const mismatch = contractVersion !== undefined && contractVersion !== CONTRACT_VERSION;

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
        <span className="live-dot" data-stalled={stalled}>
          <span>
            {updatedAtMs === null ? (
              "brak odczytu"
            ) : (
              <>
                <span className="live-verb">odświeżono </span>
                {ago(updatedAtMs, nowMs)}
                <span className="live-cadence"> · co {STATUS_REFETCH_MS / 1000} s</span>
              </>
            )}
          </span>
        </span>
      </div>
    </div>
  );
}
