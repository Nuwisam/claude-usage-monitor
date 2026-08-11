import { ApiError } from "../api/client";

export function LoadingBlock() {
  return (
    <div className="state-block">
      <h4>Reading state…</h4>
    </div>
  );
}

/** Errors are shown IN PLACE. Only `handle401` redirects, and only when the backend gives
 *  a login address.
 *
 *  403 (email outside the allowlist) and 503 (identity service unreachable) pointed at the
 *  login would loop: the login sends a signed-in user back, the backend refuses again. */
export function ErrorBlock({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const api = error instanceof ApiError ? error : null;

  const [title, body] = ((): [string, string] => {
    switch (api?.reason ?? api?.status) {
      case "not-authenticated":
      case 401:
        return [
          "You are not signed in",
          "The backend refused access and gave no login address. With AUTH_MODE=header the header from the proxy is missing; with AUTH_MODE=verify set AUTH_LOGIN_URL so there is somewhere to send you.",
        ];
      case "email-not-allowed":
      case 403:
        return [
          "Your address is not on the list",
          "Authentication succeeded, but the backend does not allow this address. Add it to ALLOWED_EMAILS in .env and restart the container.",
        ];
      case "sso-unreachable":
      case "sso-unavailable":
      case 503:
        return [
          "The session cannot be confirmed",
          "The backend cannot reach the identity service at AUTH_VERIFY_URL. Check that the address is correct and that the backend has a network route to it.",
        ];
      default:
        return [
          "The data could not be read",
          api ? `The backend returned ${api.status}${api.reason ? ` (${api.reason})` : ""}.` : String(error),
        ];
    }
  })();

  return (
    <div className="state-block">
      <h4>{title}</h4>
      <p>{body}</p>
      {onRetry && (
        <button className="btn btn-primary" onClick={onRetry} style={{ alignSelf: "flex-start" }}>
          Try again
        </button>
      )}
    </div>
  );
}
