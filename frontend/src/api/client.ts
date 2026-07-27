/** Backend jest jedyna brama SSO — nikt nie zwroci 302. Brak sesji to
 *  `401 {detail:{reason, redirect_url}}`, a przekierowanie robi ten plik. */
import type { ApiErrorBody, HistoryResponse, StatusResponse } from "./types";

/** Vite podstawia tu `base` z vite.config.ts. Jedna wartosc dla routera i dla API. */
const BASE = import.meta.env.BASE_URL.replace(/\/$/, "");
const API = `${BASE}/api`;

export const MOCKS = import.meta.env.VITE_MOCKS === "1";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly reason: string | null,
    message?: string,
  ) {
    super(message ?? `HTTP ${status}${reason ? ` (${reason})` : ""}`);
    this.name = "ApiError";
  }
}

function reasonOf(body: unknown): string | null {
  const b = body as ApiErrorBody | null;
  if (!b) return null;
  if (typeof b.detail === "object" && b.detail?.reason) return b.detail.reason;
  if (typeof b.reason === "string") return b.reason;
  return null;
}

/** Obietnica, ktora nigdy sie nie rozwiazuje — React Query zamiera zamiast migotac
 *  bledem w trakcie wychodzenia ze strony. */
function leaveTo(url: string): Promise<never> {
  window.location.assign(url);
  return new Promise<never>(() => {});
}

async function handle401(body: unknown): Promise<never> {
  const b = body as ApiErrorBody | null;
  const redirect = typeof b?.detail === "object" ? b.detail?.redirect_url : null;
  if (redirect) return leaveTo(redirect);
  // `location.href` niesie prefiks, wiec powrot trafia w aplikacje, nie w korzen serwisu.
  return leaveTo(`/oauth2/start?rd=${encodeURIComponent(window.location.href)}`);
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });

  if (res.ok) return (await res.json()) as T;

  const body = await res.json().catch(() => null);
  // Tylko 401 nawiguje — 403/429/503 w miejscu, inaczej awaria SSO daje petle przekierowan.
  if (res.status === 401) return handle401(body);
  throw new ApiError(res.status, reasonOf(body));
}

export async function fetchStatus(): Promise<StatusResponse> {
  if (MOCKS) return (await import("../mocks/status")).mockStatus();
  return getJson<StatusResponse>("/status");
}

export interface HistoryQuery {
  account: string;
  seriesId: number;
  from: Date;
  to: Date;
  bucket?: string;
}

export async function fetchHistory(q: HistoryQuery): Promise<HistoryResponse> {
  if (MOCKS) return (await import("../mocks/history")).mockHistory(q);
  const p = new URLSearchParams({
    account: q.account,
    seriesId: String(q.seriesId),
    from: q.from.toISOString(),
    to: q.to.toISOString(),
    bucket: q.bucket ?? "auto",
  });
  return getJson<HistoryResponse>(`/history?${p.toString()}`);
}
