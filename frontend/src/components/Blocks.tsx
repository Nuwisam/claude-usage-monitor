import { ApiError } from "../api/client";

export function LoadingBlock() {
  return (
    <div className="state-block">
      <h4>Czytam stan…</h4>
    </div>
  );
}

/** Błędy pokazujemy W MIEJSCU. Przekierowanie robi wyłącznie `handle401`, i tylko wtedy,
 *  gdy backend poda adres logowania.
 *
 *  403 (adres poza allowlistą) i 503 (usługa tożsamości nieosiągalna) skierowane na
 *  logowanie dałyby pętlę: logowanie odsyła zalogowanego użytkownika, backend znów odmawia. */
export function ErrorBlock({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const api = error instanceof ApiError ? error : null;

  const [title, body] = ((): [string, string] => {
    switch (api?.reason ?? api?.status) {
      case "not-authenticated":
      case 401:
        return [
          "Nie jesteś zalogowany",
          "Backend odmówił dostępu i nie podał adresu logowania. Przy AUTH_MODE=header brakuje nagłówka od proxy; przy AUTH_MODE=verify ustaw AUTH_LOGIN_URL, żeby było dokąd odesłać.",
        ];
      case "email-not-allowed":
      case 403:
        return [
          "Twój adres nie jest na liście",
          "Uwierzytelnienie się powiodło, ale backend nie dopuszcza tego adresu. Dopisz go do ALLOWED_EMAILS w .env i podnieś kontener.",
        ];
      case "sso-unreachable":
      case "sso-unavailable":
      case 503:
        return [
          "Nie mogę potwierdzić sesji",
          "Backend nie dosięga usługi tożsamości z AUTH_VERIFY_URL. Sprawdź, czy adres jest poprawny i czy backend ma do niego drogę siecią.",
        ];
      default:
        return [
          "Nie udało się odczytać danych",
          api ? `Backend odpowiedział ${api.status}${api.reason ? ` (${api.reason})` : ""}.` : String(error),
        ];
    }
  })();

  return (
    <div className="state-block">
      <h4>{title}</h4>
      <p>{body}</p>
      {onRetry && (
        <button className="btn btn-primary" onClick={onRetry} style={{ alignSelf: "flex-start" }}>
          Spróbuj ponownie
        </button>
      )}
    </div>
  );
}
