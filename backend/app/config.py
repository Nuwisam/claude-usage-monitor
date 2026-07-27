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

    @property
    def allowed_emails(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_emails_raw.split(",") if e.strip()}

    @property
    def ingest_tokens(self) -> dict[str, str]:
        """token -> nazwa maszyny."""
        out: dict[str, str] = {}
        for part in self.ingest_tokens_raw.split(","):
            part = part.strip()
            if not part:
                continue
            token, _, machine = part.partition(":")
            token = token.strip()
            if token:
                out[token] = (machine.strip() or "unknown")
        return out


settings = Settings()
