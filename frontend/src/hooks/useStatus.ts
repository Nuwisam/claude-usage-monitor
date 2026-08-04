import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { fetchStatus } from "../api/client";
import { serverOffsetMs } from "../lib/time";

/** /status co 15 s — tempo z handoutu. Dane i tak przyrastaja tylko wtedy, gdy pracujesz;
 *  czestsze pytanie nic nie kupuje, a kazde odpytanie przechodzi przez brame autoryzacji. */
export const STATUS_REFETCH_MS = 15_000;

/** With SSE connected the poll stops being the way new data arrives and becomes a safety
 *  net: it recomputes freshness while the client is silent, owns `warnings[]`, patches
 *  frames lost to a broken connection, and is the only thing that discovers accounts
 *  created after the stream was opened. Hence slower, not gone.
 *
 *  3 min rather than 60 s: the probe is throttled to at most one measurement per 60 s, so
 *  a minute-long poll could not surface anything the stream had not already delivered.
 *  What the poll is actually for changes with time alone, not with new data, and the
 *  fastest of those transitions is `live -> stale` at FRESH_WINDOW_SEC = 300 s — so the
 *  worst case is a series showing `live` for 8 minutes instead of 5. The error is bounded
 *  and points the harmless way: `stale` is explicitly non-alarming, and in a running
 *  window utilization only ever rises, so the number shown stays a lower bound. */
export const STATUS_REFETCH_LIVE_MS = 180_000;

/** Whether the SSE stream is currently delivering.
 *
 *  Module-level rather than context on purpose: three components call `useStatus()` on the
 *  SAME query key, and React Query schedules a query at its fastest observer's interval —
 *  so a per-caller argument would leave the slowest one moot and the poll pinned at 15 s.
 *  This is one app-wide fact with one writer (`useLiveStream`); React Query re-reads it on
 *  every reschedule, so a change lands within one interval.
 */
let streamLive = false;

export function setStreamLive(value: boolean): void {
  streamLive = value;
}

export function useStatus() {
  return useQuery({
    queryKey: ["status"],
    queryFn: fetchStatus,
    // While the stream is live React Query's own interval is OFF and the safety net is a
    // plain timer in `useLiveStream`. Reason: `setQueryData` triggers onQueryUpdate ->
    // updateTimers -> updateRefetchInterval, which unconditionally clears and recreates
    // the interval. A frame arriving more often than the interval would therefore keep
    // postponing the poll forever — and a frame only refreshes ONE account, so a busy
    // account would indefinitely mask a silent one, freezing its card on `live`.
    refetchInterval: () => (streamLive ? false : STATUS_REFETCH_MS),
    refetchOnWindowFocus: true,
    staleTime: 0,
    retry: 1,
  });
}

/** Tykanie sekundowe do countdownow. Osobno od zapytania, bo countdown ma iść co sekunde,
 *  a dane przychodza co 15 s — inaczej licznik staby skokami po 15 s. */
export function useTick(intervalMs = 1000): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(id);
  }, [intervalMs]);
  return now;
}

/** Zegar serwera: przesuniecie liczone raz na odpowiedz, potem tykane lokalnie.
 *  Wszystkie countdowny w UI licza sie z tego, nigdy z `new Date()`. */
export function useServerClock(serverNow: string | undefined): number {
  const tick = useTick();
  const [offset, setOffset] = useState(0);
  useEffect(() => {
    if (serverNow) setOffset(serverOffsetMs(serverNow));
  }, [serverNow]);
  return tick + offset;
}
