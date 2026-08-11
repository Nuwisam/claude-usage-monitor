/** The backend is the only SSO gate — nobody will return a 302. No session means
 *  `401 {detail:{reason, redirect_url}}`, and the redirect is done by this file. */
import type { ApiErrorBody, HistoryResponse, StatusResponse } from "./types";

/** Vite substitutes `base` from vite.config.ts here. One value for the router and for the API. */
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

/** A promise that never resolves — React Query freezes instead of flashing an error
 *  while the page is navigating away. */
function leaveTo(url: string): Promise<never> {
  window.location.assign(url);
  return new Promise<never>(() => {});
}

/** A 401 with a login address = redirect. A 401 without one = an error in place.
 *
 *  We do not guess a fallback address. The backend knows whether it sits behind anything
 *  that logs users in, and the UI does not — sending the user by default to some typical
 *  path ends in a 404 or somebody else's login page and looks like an app failure. */
async function handle401(body: unknown): Promise<never> {
  const b = body as ApiErrorBody | null;
  const redirect = typeof b?.detail === "object" ? b.detail?.redirect_url : null;
  if (redirect) return leaveTo(redirect);
  throw new ApiError(401, reasonOf(body));
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });

  if (res.ok) return (await res.json()) as T;

  const body = await res.json().catch(() => null);
  // Only a 401 navigates — 403/429/503 in place, otherwise an SSO failure gives a redirect loop.
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
