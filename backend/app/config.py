from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str = Field(..., alias="DATABASE_URL")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # --- Autoryzacja dostepu do UI/API ---
    # Wymagane, bez wartosci domyslnej: kazda domyslna bylaby zla odpowiedzia. `none`
    # otwieralby dane po cichu u kogos, kto po prostu nie doczytal; cokolwiek innego
    # przewracaloby instalacje lokalna bez zadnego proxy przed soba. Niech instalujacy
    # powie wprost, co ma sie stac.
    #
    # Typ `Literal`, nie `str` — Compose podstawia PUSTY CIAG za nieustawiona zmienna
    # srodowiskowa, a `str` przyjalby go bez slowa i wpadlby w gałąź "nic nie pasuje".
    # `Literal` jest dopasowaniem doslownym, wiec pusta wartosc konczy sie bledem startu.
    auth_mode: Literal["none", "header", "verify"] = Field(..., alias="AUTH_MODE")

    # `header`: proxy juz uwierzytelnilo i podaje adres dalej. Wolno tylko za proxy, ktore
    # ten naglowek USUWA z zadan przychodzacych — inaczej kazdy nazwie sie kim zechce.
    auth_email_header: str = Field("X-Forwarded-Email", alias="AUTH_EMAIL_HEADER")

    # `verify`: uslugą tozsamosci pytamy przez JSON, kim jest wolajacy. Nazwy pol
    # odpowiedzi sa konfiguracja, bo kazdy dostawca opisuje je inaczej. Puste = pole
    # nieczytane.
    auth_verify_url: str = Field("", alias="AUTH_VERIFY_URL")
    auth_email_field: str = Field("email", alias="AUTH_EMAIL_FIELD")
    auth_verified_at_field: str = Field("", alias="AUTH_VERIFIED_AT_FIELD")
    auth_redirect_field: str = Field("", alias="AUTH_REDIRECT_FIELD")

    # Adres logowania oddawany przegladarce przy 401, gdy usluga tozsamosci nie podala
    # zadnego sama (typowo: odpowiada strona HTML, nie JSON-em). `{rd}` zostaje zastapione
    # zakodowanym adresem powrotu. Puste = nie ma dokad odeslac i UI tak wlasnie powie.
    auth_login_url: str = Field("", alias="AUTH_LOGIN_URL")

    # Lista po przecinku. Pusty zbior = deny all (fail-safe). Nie dotyczy `AUTH_MODE=none`,
    # gdzie zadnego adresu po prostu nie ma.
    allowed_emails_raw: str = Field("", alias="ALLOWED_EMAILS")
    # Sklada adres powrotu dla `{rd}`. Ten sam origin, pod ktorym stoi aplikacja.
    public_origin: str = Field("http://localhost:8080", alias="PUBLIC_ORIGIN")
    app_base_path: str = Field("/claude-usage", alias="APP_BASE_PATH")

    # --- Ingest ---
    # "token:maszyna,token2:maszyna2" — token identyfikuje MASZYNE, nie konto.
    ingest_tokens_raw: str = Field("", alias="INGEST_TOKENS")
    max_ingest_body_bytes: int = Field(262144, alias="MAX_INGEST_BODY_BYTES")
    max_backlog_entries: int = Field(200, alias="MAX_BACKLOG_ENTRIES")
    # Prog ZDARZENIA `clock_skew`, nic wiecej. Datowanie stoi na roznicy `sent_at - ts`
    # w obrebie zegara klienta, wiec rozjazd wzgledem serwera go nie psuje i nie ma powodu,
    # zeby cokolwiek na nim odrzucac.
    #
    # BACKLOG_MAX_AGE_SEC usuniete: podstawialo czas serwera pod pomiar starszy niz tydzien,
    # czyli robilo odwrotnosc ochrony — taki wpis stawal sie najnowszy i przejmowal stan
    # biezacy. Objetosc ograniczaja MAX_SPOOL_LINES, MAX_BACKLOG_PER_REQUEST po stronie
    # sondy i MAX_BACKLOG_ENTRIES tutaj.
    clock_skew_tolerance_sec: int = Field(300, alias="CLOCK_SKEW_TOLERANCE_SEC")

    # --- Dedup i spojnosc ---
    sample_heartbeat_sec: int = Field(300, alias="SAMPLE_HEARTBEAT_SEC")
    monotonic_eps: float = Field(0.5, alias="MONOTONIC_EPS")
    # Do jakiej roznicy `resets_at` uznajemy, ze to TO SAMO okno. Granica podawana przez
    # Anthropic kolysze sie o ~2 s; najkrotsze okno ma 5 h. Patrz parsing.same_reset_window.
    reset_window_eps_sec: int = Field(300, alias="RESET_WINDOW_EPS_SEC")

    # --- Swiezosc ---
    fresh_window_sec: int = Field(300, alias="FRESH_WINDOW_SEC")
    client_silent_sec: int = Field(21600, alias="CLIENT_SILENT_SEC")

    # --- Historia ---
    # Od jakiej przerwy uznajemy dziure w danych. Throttle sondy to 60 s, heartbeat 300 s,
    # wiec 15 min to kilkukrotnosc normalnego odstepu — nie zglasza szumu.
    history_gap_sec: int = Field(900, alias="HISTORY_GAP_SEC")

    # --- SSE stream ---
    # "token:label,token2:label2" — credential for headless clients (AX206 panel,
    # scripts). A SEPARATE secret from INGEST_TOKENS: that one is WRITE-only and is
    # already handed out on every machine running the probe. Accepting it here would
    # silently widen the meaning of a key that is in circulation and that nobody would
    # rotate for this reason.
    stream_tokens_raw: str = Field("", alias="STREAM_TOKENS")
    # Seconds, float on purpose: tests drive sub-second streams, and a duration is not
    # inherently an integer. The ping keeps the connection alive through Apache, but its
    # more important job is local: both middlewares in main.py are BaseHTTPMiddleware,
    # which on a streaming response can delay client-disconnect detection until the next
    # write. Without the ping a dead subscription would sit in the broker indefinitely.
    stream_ping_sec: float = Field(15.0, alias="STREAM_PING_SEC")
    # Hard connection lifetime. This is the ONLY moment an SSO session is re-verified on
    # a long stream — without it an expired cookie would keep feeding data forever.
    # EventSource reconnects on its own, so the browser never notices.
    stream_max_lifetime_sec: float = Field(900.0, alias="STREAM_MAX_LIFETIME_SEC")
    stream_max_clients: int = Field(32, alias="STREAM_MAX_CLIENTS")
    stream_queue_max: int = Field(32, alias="STREAM_QUEUE_MAX")

    @property
    def allowed_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails_raw.split(",") if e.strip()}

    @property
    def ingest_tokens(self) -> dict[str, str]:
        """token -> nazwa maszyny."""
        return _parse_tokens(self.ingest_tokens_raw)

    @property
    def stream_tokens(self) -> dict[str, str]:
        """token -> stream client label."""
        return _parse_tokens(self.stream_tokens_raw)


def _parse_tokens(raw: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        token, _, label = part.partition(":")
        token = token.strip()
        if token:
            out[token] = (label.strip() or "unknown")
    return out


settings = Settings()
