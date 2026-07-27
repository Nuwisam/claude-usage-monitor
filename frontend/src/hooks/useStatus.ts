import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { fetchStatus } from "../api/client";
import { serverOffsetMs } from "../lib/time";

/** /status co 15 s — tempo z handoutu. Dane i tak przyrastaja tylko wtedy, gdy pracujesz;
 *  czestsze pytanie nic nie kupuje, a kazde odpytanie to round-trip do oauth2-proxy. */
export const STATUS_REFETCH_MS = 15_000;

export function useStatus() {
  return useQuery({
    queryKey: ["status"],
    queryFn: fetchStatus,
    refetchInterval: STATUS_REFETCH_MS,
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
