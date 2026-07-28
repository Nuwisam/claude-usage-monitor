import { useMemo } from "react";
import { Route, Routes } from "react-router-dom";

import { Nav } from "./components/Nav";
import { useLiveStream } from "./hooks/useLiveStream";
import { useServerClock, useStatus } from "./hooks/useStatus";
import { History } from "./views/History";
import { Live } from "./views/Live";

export function App() {
  // Ten sam klucz zapytania co w widokach, wiec React Query oddaje ten sam cache —
  // naglowek nie generuje drugiego odpytania.
  const q = useStatus();
  const nowMs = useServerClock(q.data?.serverNow);

  // The first /status brings the account list; the stream then subscribes to exactly those
  // UUIDs. An account created later (a `/login` onto a fresh one) shows up in the next
  // poll, the key changes, and the stream reconnects with it included.
  const uuids = useMemo(
    () => (q.data?.accounts ?? []).map((a) => a.uuid),
    [q.data?.accounts],
  );
  const stream = useLiveStream(uuids);

  return (
    <div className="app">
      <Nav
        contractVersion={q.data?.contractVersion}
        updatedAtMs={q.dataUpdatedAt || null}
        nowMs={nowMs}
        stalled={q.isError || q.isPaused}
        stream={stream}
      />
      <Routes>
        <Route path="/" element={<Live />} />
        <Route path="/historia" element={<History />} />
        <Route path="*" element={<Live />} />
      </Routes>
    </div>
  );
}
