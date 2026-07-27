import { ApiError } from "../api/client";

export function LoadingBlock() {
  return (
    <div className="state-block">
      <h4>Czytam stan…</h4>
    </div>
  );
}

/** Bledy inne niz 401 pokazujemy W MIEJSCU, bez przekierowania.
 *  403 (email poza allowlista) i 503 (oauth2-proxy nieosiagalny) skierowane na logowanie
 *  daja petle: SSO odsyla zalogowanego uzytkownika, backend znow odmawia. */
export function ErrorBlock({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const api = error instanceof ApiError ? error : null;

  const [title, body] = ((): [string, string] => {
    switch (api?.reason ?? api?.status) {
      case "email-not-allowed":
      case 403:
        return [
          "Twój adres nie jest na liście",
          "Sesja SSO jest poprawna, ale backend nie dopuszcza tego adresu. Dopisz go do ALLOWED_EMAILS w .env i podnieś kontener.",
        ];
      case "sso-unreachable":
      case "sso-unavailable":
      case 503:
        return [
          "Nie mogę potwierdzić sesji",
          "Backend nie dosięga oauth2-proxy. Sprawdź, czy kontener identity_proxy żyje i czy backend jest w sieci identity-proxy_default.",
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
