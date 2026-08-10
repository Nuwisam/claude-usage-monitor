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

/** "15 s" / "3 min" — the cadence in the header. Local, because this is the only place with
 *  this form; `ago` and `countdown` from lib/time mean something else ("ago", "past reset"). */
function every(ms: number): string {
  return ms >= 60_000 ? `${Math.round(ms / 60_000)} min` : `${Math.round(ms / 1000)} s`;
}

export function Nav({ contractVersion, updatedAtMs, nowMs, stalled, stream }: Props) {
  const mismatch = contractVersion !== undefined && contractVersion !== CONTRACT_VERSION;
  // Name outright where the data comes from. "live" means the measurement shows at once;
  // with a broken stream it is honester to show the polling rate than to fake a push.
  const cadence =
    stream === "live"
      ? `live · check every ${every(STATUS_REFETCH_LIVE_MS)}`
      : `every ${every(STATUS_REFETCH_MS)}`;

  return (
    <div className="nav app-nav">
      <span className="nav-brand">Claude Usage</span>
      <NavLink to="/" end>
        Live
      </NavLink>
      <NavLink to="/history">History</NavLink>

      <div className="app-nav-right">
        <span
          className="pill-contract"
          data-mismatch={mismatch}
          title={
            mismatch
              ? `UI built against contract v${CONTRACT_VERSION}, the backend says v${contractVersion} — the data may be rendered wrongly`
              : "API contract version"
          }
        >
          contract v{contractVersion ?? "?"}
          {mismatch ? ` ≠ v${CONTRACT_VERSION}` : ""}
        </span>
        {/* The whole caption in ONE span: `.live-dot` is a flex with `gap`, so splitting it
            into siblings would push the spacing between words apart. Narrow leaves just
            "3 s ago" (mockup 2b) — @media hides the rest. */}
        <span className="live-dot" data-stalled={stalled} data-stream={stream}>
          <span>
            {updatedAtMs === null ? (
              "no reading"
            ) : (
              <>
                <span className="live-verb">refreshed </span>
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
