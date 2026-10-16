import { useMemo } from "react";
import { Route, Routes } from "react-router-dom";

import { Nav } from "./components/Nav";
import { useLiveStream } from "./hooks/useLiveStream";
import { useServerClock, useStatus } from "./hooks/useStatus";
import { History } from "./views/History";
import { Live } from "./views/Live";

export function App() {
  // The same query key as in the views, so React Query hands back the same cache —
  // the header does not generate a second request.
  const q = useStatus();
  const nowMs = useServerClock(q.data?.serverNow);

  // The first /status brings the account list; the stream then subscribes to exactly those
  // keys. An account created later (a `/login` onto a fresh one) shows up in the next
  // poll, the set changes, and the stream reconnects with it included.
  const keys = useMemo(
    () => (q.data?.accounts ?? []).map((a) => a.key),
    [q.data?.accounts],
  );
  const stream = useLiveStream(keys);

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
        <Route path="/history" element={<History />} />
        <Route path="*" element={<Live />} />
      </Routes>
    </div>
  );
}
