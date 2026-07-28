from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str = Field(..., alias="DATABASE_URL")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # --- SSO (oauth2-proxy osiagalny w sieci identity-proxy_default) ---
    sso_verify_url: str = Field(
        "http://identity_proxy:8080/api/verify", alias="SSO_VERIFY_URL"
    )
    # Lista po przecinku. Pusty zbior = deny all (fail-safe).
    allowed_emails_raw: str = Field("", alias="ALLOWED_EMAILS")
    # Potrzebne do zbudowania URL-a logowania, bo oauth2-proxy zwraca HTML bez redirect_url.
    public_origin: str = Field("https://usage.example.org", alias="PUBLIC_ORIGIN")
    app_base_path: str = Field("/claude-usage", alias="APP_BASE_PATH")

    # --- Ingest ---
    # "token:maszyna,token2:maszyna2" — token identyfikuje MASZYNE, nie konto.
    ingest_tokens_raw: str = Field("", alias="INGEST_TOKENS")
    max_ingest_body_bytes: int = Field(262144, alias="MAX_INGEST_BODY_BYTES")
    max_backlog_entries: int = Field(200, alias="MAX_BACKLOG_ENTRIES")
    clock_skew_tolerance_sec: int = Field(300, alias="CLOCK_SKEW_TOLERANCE_SEC")
    backlog_max_age_sec: int = Field(604800, alias="BACKLOG_MAX_AGE_SEC")

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
