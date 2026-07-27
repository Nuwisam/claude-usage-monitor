import { Route, Routes } from "react-router-dom";

import { Nav } from "./components/Nav";
import { useServerClock, useStatus } from "./hooks/useStatus";
import { History } from "./views/History";
import { Live } from "./views/Live";

export function App() {
  // Ten sam klucz zapytania co w widokach, wiec React Query oddaje ten sam cache —
  // naglowek nie generuje drugiego odpytania.
  const q = useStatus();
  const nowMs = useServerClock(q.data?.serverNow);

  return (
    <div className="app">
      <Nav
        contractVersion={q.data?.contractVersion}
        updatedAtMs={q.dataUpdatedAt || null}
        nowMs={nowMs}
        stalled={q.isError || q.isPaused}
      />
      <Routes>
        <Route path="/" element={<Live />} />
        <Route path="/historia" element={<History />} />
        <Route path="*" element={<Live />} />
      </Routes>
    </div>
  );
}
