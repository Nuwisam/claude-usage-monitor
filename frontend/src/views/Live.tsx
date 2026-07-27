import { AccountCard } from "../components/AccountCard";
import { Warnings } from "../components/Warnings";
import { useServerClock, useStatus } from "../hooks/useStatus";
import { ErrorBlock, LoadingBlock } from "../components/Blocks";

export function Live() {
  const q = useStatus();
  const nowMs = useServerClock(q.data?.serverNow);

  if (q.isLoading) return <LoadingBlock />;
  if (q.error || !q.data) return <ErrorBlock error={q.error} onRetry={() => q.refetch()} />;

  return (
    <>
      <Warnings items={q.data.warnings} />

      <div className="accounts">
        {q.data.accounts.map((a) => (
          <AccountCard key={a.uuid} a={a} nowMs={nowMs} />
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
