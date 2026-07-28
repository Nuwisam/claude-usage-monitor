/** SSE subscription to the accounts currently on screen.
 *
 *  A frame carries the FULL `AccountStatus`, so applying it is a cache splice — no refetch,
 *  no extra round-trip to oauth2-proxy. The poll stays alive as a much slower safety net
 *  (see `STATUS_REFETCH_LIVE_MS`): it owns `warnings[]`, it is what recomputes freshness
 *  while the client is silent, and it is the only thing that discovers accounts that did
 *  not exist when the stream was opened.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { MOCKS } from "../api/client";
import type { AccountFrame, StatusResponse } from "../api/types";
import { STATUS_REFETCH_LIVE_MS, setStreamLive } from "./useStatus";

const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");

/** connecting — opened or reconnecting after the server's lifetime cap ·
 *  live — frames are flowing · down — no stream, the poll carries everything. */
export type StreamState = "connecting" | "live" | "down";

export function useLiveStream(uuids: string[]): StreamState {
  const qc = useQueryClient();
  const [state, setState] = useState<StreamState>("down");
  // Sorted + joined: `uuids` is a fresh array on every /status refetch, so depending on the
  // array itself would tear the connection down four times a minute. The key only changes
  // when the SET of accounts changes — which is exactly when we do want to reconnect.
  const key = [...uuids].sort().join(",");
  // `bye` is the server closing on its own lifetime cap, and EventSource reconnects by
  // itself. Without this flag the expected reconnect would flash "down" in the header
  // every 15 minutes and look like a fault.
  const reconnecting = useRef(false);

  useEffect(() => {
    if (MOCKS || !key) {
      setState("down");
      return;
    }
    const params = key
      .split(",")
      .map((u) => `account=${encodeURIComponent(u)}`)
      .join("&");
    // snapshot=0 — the browser has just fetched /api/status, so the initial cards would
    // only duplicate what the cache already holds.
    const es = new EventSource(`${BASE}/api/stream?${params}&snapshot=0`);
    setState("connecting");

    const alive = () => {
      reconnecting.current = false;
      setState("live");
    };
    es.addEventListener("hello", alive);
    es.addEventListener("ping", alive);

    es.addEventListener("account", (e) => {
      const frame = JSON.parse((e as MessageEvent).data) as AccountFrame;
      qc.setQueryData<StatusResponse>(["status"], (prev) => {
        // No baseline yet — the first /status is still in flight and will carry this
        // account anyway. Inventing a response here would mean shipping a StatusResponse
        // with no `warnings` and a guessed `contractVersion`.
        if (!prev) return prev;
        const known = prev.accounts.some((a) => a.uuid === frame.account.uuid);
        return {
          ...prev,
          // `serverNow` moves with the frame so every countdown stays anchored to the
          // server clock rather than drifting on the local one.
          serverNow: frame.serverNow,
          accounts: known
            ? prev.accounts.map((a) =>
                a.uuid === frame.account.uuid ? frame.account : a,
              )
            : [...prev.accounts, frame.account],
        };
      });
      alive();
    });

    // Frames were dropped because this client fell behind. Every frame is a full snapshot,
    // so there is nothing to replay — one refetch restores the truth.
    es.addEventListener("lag", () => {
      void qc.invalidateQueries({ queryKey: ["status"] });
    });
    es.addEventListener("bye", () => {
      reconnecting.current = true;
      setState("connecting");
    });
    es.onerror = () => setState(reconnecting.current ? "connecting" : "down");

    return () => es.close();
  }, [key, qc]);

  useEffect(() => {
    setStreamLive(state === "live");
  }, [state]);

  // The safety net, and the reason it is a timer of our own rather than React Query's
  // `refetchInterval`: the built-in one is cleared and recreated on every cache write, so
  // frames arriving more often than the interval would postpone it indefinitely. This
  // effect depends only on `state`, and a frame re-setting it to the same "live" value
  // does not re-run it — so the heartbeat cannot be pushed back by traffic.
  //
  // That distinction is not cosmetic. A frame refreshes ONE account; freshness for every
  // other account is only ever recomputed by /status. Without an unpostponable heartbeat,
  // one busy account would keep a silent one frozen on `freshness: "live"` — a full bar
  // and a confident number over data hours old.
  useEffect(() => {
    if (state !== "live") return;
    const id = window.setInterval(() => {
      // Match React Query's default of not polling a hidden tab; `refetchOnWindowFocus`
      // covers the return.
      if (document.visibilityState === "visible") {
        void qc.refetchQueries({ queryKey: ["status"] });
      }
    }, STATUS_REFETCH_LIVE_MS);
    return () => window.clearInterval(id);
  }, [state, qc]);

  return state;
}
