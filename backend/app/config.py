from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str = Field(..., alias="DATABASE_URL")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    # --- Authorization of access to the UI/API ---
    # Required, with no default: every default would be the wrong answer. `none` would
    # silently open the data up for someone who simply did not read this far; anything
    # else would knock over a local deployment that has no proxy in front of it. Let
    # whoever installs it say outright what is supposed to happen.
    #
    # Type `Literal`, not `str` — Compose substitutes an EMPTY STRING for an unset
    # environment variable, and `str` would accept it without a word and fall into the
    # "nothing matches" branch. `Literal` is a literal match, so an empty value ends in
    # a startup error.
    auth_mode: Literal["none", "header", "verify"] = Field(..., alias="AUTH_MODE")

    # `header`: the proxy has already authenticated and passes the email address on. Allowed only
    # behind a proxy that STRIPS this header from incoming requests — otherwise anyone calls
    # themselves whoever they like.
    auth_email_header: str = Field("X-Forwarded-Email", alias="AUTH_EMAIL_HEADER")

    # `verify`: we ask the identity service over JSON who the caller is. The names of the
    # response fields are configuration, because every provider describes them differently.
    # Empty = the field is not read.
    auth_verify_url: str = Field("", alias="AUTH_VERIFY_URL")
    auth_email_field: str = Field("email", alias="AUTH_EMAIL_FIELD")
    auth_verified_at_field: str = Field("", alias="AUTH_VERIFIED_AT_FIELD")
    auth_redirect_field: str = Field("", alias="AUTH_REDIRECT_FIELD")

    # Login URL handed back to the browser on a 401, when the identity service gave none
    # itself (typically: it answers with an HTML page, not with JSON). `{rd}` is replaced by
    # the encoded return URL. Empty = there is nowhere to send anyone back to, and the UI
    # says exactly that.
    auth_login_url: str = Field("", alias="AUTH_LOGIN_URL")

    # Comma-separated list. An empty set = deny all (fail-safe). Does not apply to
    # `AUTH_MODE=none`, where there is simply no email address at all.
    allowed_emails_raw: str = Field("", alias="ALLOWED_EMAILS")
    # Builds the return URL for `{rd}`. The same origin the application is served from.
    public_origin: str = Field("http://localhost:8080", alias="PUBLIC_ORIGIN")
    app_base_path: str = Field("/claude-usage", alias="APP_BASE_PATH")

    # --- Ingest ---
    # "token:machine,token2:machine2" — the token identifies the MACHINE, not the account.
    ingest_tokens_raw: str = Field("", alias="INGEST_TOKENS")
    max_ingest_body_bytes: int = Field(262144, alias="MAX_INGEST_BODY_BYTES")
    max_backlog_entries: int = Field(200, alias="MAX_BACKLOG_ENTRIES")
    # Threshold for the `clock_skew` EVENT, nothing more. Timestamping rests on the `sent_at - ts`
    # difference within the client's own clock, so drift against the server does not break it
    # and there is no reason to reject anything on account of it.
    #
    # BACKLOG_MAX_AGE_SEC removed: it substituted server time for a measurement older than
    # a week, that is, it did the opposite of protecting — such an entry became the newest one
    # and took over the current state. Volume is bounded by MAX_SPOOL_LINES and
    # MAX_BACKLOG_PER_REQUEST on the probe side and by MAX_BACKLOG_ENTRIES here.
    clock_skew_tolerance_sec: int = Field(300, alias="CLOCK_SKEW_TOLERANCE_SEC")

    # --- Dedup and consistency ---
    sample_heartbeat_sec: int = Field(300, alias="SAMPLE_HEARTBEAT_SEC")
    monotonic_eps: float = Field(0.5, alias="MONOTONIC_EPS")
    # Up to what `resets_at` difference we call it the SAME window. The boundary reported by
    # Anthropic wobbles by ~2 s; the shortest window is 5 h. See parsing.same_reset_window.
    reset_window_eps_sec: int = Field(300, alias="RESET_WINDOW_EPS_SEC")

    # --- Freshness ---
    fresh_window_sec: int = Field(300, alias="FRESH_WINDOW_SEC")
    client_silent_sec: int = Field(21600, alias="CLIENT_SILENT_SEC")

    # --- History ---
    # How long a break has to be before we call it a gap in the data. The probe's throttle is
    # 60 s and the heartbeat 300 s, so 15 min is several times the normal interval — it does
    # not report noise.
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
        """token -> machine name."""
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
