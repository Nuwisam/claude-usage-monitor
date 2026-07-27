import { useEffect, useState } from "react";

import { AccountCard } from "../components/AccountCard";
import { Warnings } from "../components/Warnings";
import { useServerClock, useStatus } from "../hooks/useStatus";
import { ErrorBlock, LoadingBlock } from "../components/Blocks";

/** Przelaczniki widoku zyja w localStorage — po odswiezeniu ekran wraca taki, jaki byl. */
function useSticky(key: string, initial: boolean) {
  const [v, setV] = useState<boolean>(() => {
    const raw = window.localStorage.getItem(key);
    return raw === null ? initial : raw === "1";
  });
  useEffect(() => {
    window.localStorage.setItem(key, v ? "1" : "0");
  }, [key, v]);
  return [v, setV] as const;
}

export function Live() {
  const q = useStatus();
  const nowMs = useServerClock(q.data?.serverNow);
  const [showDuplicates, setShowDuplicates] = useSticky("cum.showDuplicates", false);
  const [showKeys, setShowKeys] = useSticky("cum.showKeys", false);

  if (q.isLoading) return <LoadingBlock />;
  if (q.error || !q.data) return <ErrorBlock error={q.error} onRetry={() => q.refetch()} />;

  return (
    <>
      <Warnings items={q.data.warnings} />

      <div className="view-toggles">
        <label className="radio check">
          <input
            type="checkbox"
            checked={showDuplicates}
            onChange={(e) => setShowDuplicates(e.target.checked)}
          />
          <span className="dot" />
          <span>duplikaty serii</span>
        </label>
        <label className="radio check">
          <input
            type="checkbox"
            checked={showKeys}
            onChange={(e) => setShowKeys(e.target.checked)}
          />
          <span className="dot" />
          <span>seriesKey</span>
        </label>
      </div>

      <div className="accounts">
        {q.data.accounts.map((a) => (
          <AccountCard
            key={a.uuid}
            a={a}
            nowMs={nowMs}
            showDuplicates={showDuplicates}
            showKeys={showKeys}
          />
        ))}
      </div>

      {q.data.accounts.length === 0 && (
        <div className="state-block">
          <h4>Brak kont</h4>
          <p>
            Żadna maszyna jeszcze nic nie zaraportowała. Konta tworzą się same przy pierwszym
            pomiarze — sprawdź, czy sonda jest wpięta w hooki i czy ma <code>config.json</code>.
          </p>
        </div>
      )}
    </>
  );
}
