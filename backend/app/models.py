"""Data model.

Governing rule: **an open set of series**. Step 0 showed 17 top-level keys in the
/api/oauth/usage response, 5 of which were known neither from the validator in the Claude
Code binary nor from the reference repo (amber_ladder, iguana_necktie, nimbus_quill,
tangelo, omelette_promotional). A hardcoded list of buckets would have been wrong on the
day it was written — which is why series are rows in a table, not columns.

Four sources of series, all reduced to a common "usage percent 0-100":
  bucket       - top-level key with a `utilization` field     (five_hour, seven_day, ...)
  limit        - entry in `limits[]` with a `percent` field   (session, weekly_all, ...)
  extra_usage  - the `extra_usage` object, `utilization` field
  spend        - the `spend` object, `percent`  <-- on a Team account this IS the binding limit
"""
from datetime import datetime

from sqlalchemy import (
    JSON, BigInteger, Boolean, DateTime, Enum, ForeignKey, Index, Integer,
    Numeric, String, Text, func,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# Variants instead of dialect types used directly — a choice that also lets the models be
# loaded under SQLite (tests), while on MariaDB they get DATETIME(6) with microseconds.
DT6 = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
LONGTEXT = Text().with_variant(mysql.LONGTEXT, "mysql")
MEDIUMTEXT = Text().with_variant(mysql.MEDIUMTEXT, "mysql")
# SQLite does autoincrement only on INTEGER (a rowid alias), not on BIGINT.
# On MariaDB it stays BIGINT — only the tests get INTEGER.
PK_BIG = BigInteger().with_variant(Integer, "sqlite")


def _dt(**kw) -> Mapped[datetime]:
    return mapped_column(DT6, **kw)


# --------------------------------------------------------------------------- accounts
class Account(Base):
    """The natural key is account_uuid from oauthAccount, NOT the label.

    Reason: on a single machine accounts are switched through /login, and settings.json is
    shared. A static label in the configuration would assign half the samples to the wrong
    account and quietly poison the history of both — with no visible symptom at all.
    """
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_uuid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    label: Mapped[str | None] = mapped_column(String(100))          # editable custom name
    email: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str | None] = mapped_column(String(255))
    color: Mapped[str | None] = mapped_column(String(16))

    org_uuid: Mapped[str | None] = mapped_column(String(64))
    org_name: Mapped[str | None] = mapped_column(String(255))
    org_type: Mapped[str | None] = mapped_column(String(64))        # claude_max | claude_team | ...
    seat_tier: Mapped[str | None] = mapped_column(String(32))       # standard | premium (Team)
    org_rate_limit_tier: Mapped[str | None] = mapped_column(String(64))
    user_rate_limit_tier: Mapped[str | None] = mapped_column(String(64))
    subscription_type: Mapped[str | None] = mapped_column(String(32))
    extra_usage_enabled: Mapped[bool | None] = mapped_column(Boolean)

    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    first_seen_at: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6))
    last_sample_at: Mapped[datetime | None] = _dt()
    last_client_host: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6))
    updated_at: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6),
                                       onupdate=func.now(6))
    archived_at: Mapped[datetime | None] = _dt()


class Machine(Base):
    """Machine == ingest token. Serves to revoke a single machine and to answer the
    question 'where did this data come from'."""
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    host: Mapped[str | None] = mapped_column(String(128))
    # cc_version removed: it came from the probe's UA_VERSION, that is from the constant
    # "2.1.215" baked into the code. The column held the same value for every machine and
    # said nothing about the version actually running there.
    script_version: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6))
    last_seen_at: Mapped[datetime | None] = _dt()
    batches: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class MachineAccount(Base):
    """Which machine reported which account. Instead of forbidding it (a static list of
    accounts per token would drift apart, since both are used on one machine), we detect:
    the first occurrence of a new pair generates a new_account_for_token event."""
    __tablename__ = "machine_accounts"

    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    first_seen_at: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6))
    last_seen_at: Mapped[datetime | None] = _dt()
    samples: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


# --------------------------------------------------------------------------- series
SeriesSource = Enum("bucket", "limit", "extra_usage", "spend", name="series_source")


class UsageSeries(Base):
    __tablename__ = "usage_series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    source: Mapped[str] = mapped_column(SeriesSource, nullable=False)

    bucket_key: Mapped[str | None] = mapped_column(String(100))       # five_hour, tangelo, ...
    kind: Mapped[str | None] = mapped_column(String(100))             # limits[].kind
    group_key: Mapped[str | None] = mapped_column(String(100))        # limits[].group
    model_display_name: Mapped[str | None] = mapped_column(String(100))
    surface_display_name: Mapped[str | None] = mapped_column(String(100))

    display_label: Mapped[str] = mapped_column(String(200), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)

    first_seen_at: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6))
    last_seen_at: Mapped[datetime | None] = _dt()
    # A series seen only as null (e.g. seven_day_opus on an account without Opus) — we register
    # it, but mark it, so that the UI does not clutter itself up with empty charts.
    ever_non_null: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# --------------------------------------------------------------------------- ingest
class RawPayload(Base):
    """Content-addressed. The probe polls every ~120 s, and while nothing is happening the
    response is byte-identical — the hash folds that into one row and a counter. This is dedup,
    NOT compaction: every batch still points at its own exact body."""
    __tablename__ = "raw_payloads"

    id: Mapped[int] = mapped_column(PK_BIG, primary_key=True, autoincrement=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    body: Mapped[str] = mapped_column(LONGTEXT, nullable=False)
    first_seen_at: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6))
    last_seen_at: Mapped[datetime | None] = _dt()
    seen_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)


class IngestBatch(Base):
    """One row per request. Essential for telling 'client silence' from 'silence in the data' —
    without it a broken client looks exactly like no activity, and the UI shows a false 0%."""
    __tablename__ = "ingest_batches"

    id: Mapped[int] = mapped_column(PK_BIG, primary_key=True, autoincrement=True)
    received_at: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id"))

    client_host: Mapped[str | None] = mapped_column(String(128))
    config_dir_hash: Mapped[str | None] = mapped_column(String(32))
    script_version: Mapped[int | None] = mapped_column(Integer)
    hook_event: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(64))

    # http_status / request_id / rl_status removed together with version 3 of the probe: they
    # described the HTTP response from Anthropic to the PROBE'S REQUEST, and the probe sends
    # none any more. In their place — where the measurement came from and how old it was when sent.
    measurement_source: Mapped[str | None] = mapped_column(String(32))
    cache_age_s: Mapped[int | None] = mapped_column(Integer)
    fresh_age_s: Mapped[int | None] = mapped_column(Integer)
    probe_ms: Mapped[int | None] = mapped_column(Integer)
    # `arrived_at - measurement.sent_at` as used for THIS request. Together with `received_at`
    # and `limit_samples.client_captured_at` it makes the computed `captured_at` reproducible
    # from the database — otherwise only the result is left, without the arithmetic.
    clock_offset_s: Mapped[int | None] = mapped_column(Integer)

    raw_payload_id: Mapped[int | None] = mapped_column(ForeignKey("raw_payloads.id"))
    payload_sha256: Mapped[str | None] = mapped_column(String(64))
    samples_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    backlog_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error_kind: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("ix_batches_acct_time", "account_id", "received_at"),
        Index("ix_batches_time", "received_at"),
    )


class LimitSample(Base):
    """The fact table.

    A DEPARTURE FROM THE PLAN, a deliberate one: the plan assumed a composite, clustering
    primary key (account_id, series_id, captured_at, id) for a contiguous range scan. At the
    real volume — on the order of thousands of rows a day, because the probe is throttled to
    120 s and runs only while work is going on — that is premature optimization that complicates
    the ORM mapping and AUTO_INCREMENT in InnoDB. A simple PK + a composite index gives the same
    query plan at this scale.
    """
    __tablename__ = "limit_samples"

    id: Mapped[int] = mapped_column(PK_BIG, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    series_id: Mapped[int] = mapped_column(ForeignKey("usage_series.id"), nullable=False)
    # The moment of measurement in SERVER time: `min(client_captured_at + offset, received_at)`.
    captured_at: Mapped[datetime] = _dt(nullable=False)
    # The same moment in CLIENT time, before the offset. Not a duplicate: since server-side
    # timestamping, `captured_at` is a function of the REQUEST, so a repeat of the same spooled
    # entry in another request has a different `captured_at`. The idempotency guard needs a part
    # that is a function of the entry itself — and that is exactly this column.
    client_captured_at: Mapped[datetime | None] = _dt()
    batch_id: Mapped[int] = mapped_column(ForeignKey("ingest_batches.id"), nullable=False)

    # `probe` = historical data from before version 3 of the probe, when it called
    # /api/oauth/usage itself. It stays in the enum, because those rows are still in the
    # database and are still valid measurements. `statusline` and `ratelimit_headers` were
    # never shipped — statusline does not work in the VS Code extension (#55643, closed as
    # not_planned), and the headers would need a MITM proxy on one's own traffic. Removed so
    # that the enum does not promise sources that do not exist.
    source: Mapped[str] = mapped_column(
        Enum("probe", "cli_merged", "cli_usage_cache", name="sample_source"),
        nullable=False, default="cli_merged",
    )
    # ALWAYS 0..100 after normalization. The source scale differed (headers give 0.0-1.0),
    # which is why the `source` column says where it came from, and the value is already unified.
    utilization: Mapped[float | None] = mapped_column(Numeric(7, 4))
    resets_at: Mapped[datetime | None] = _dt()

    is_active: Mapped[bool | None] = mapped_column(Boolean)   # limits[].is_active
    severity: Mapped[str | None] = mapped_column(String(32))  # classification from Anthropic
    stale_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    session_id: Mapped[str | None] = mapped_column(String(64))
    extra: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (
        Index("ix_samples_series_time", "account_id", "series_id", "captured_at"),
        # For the backlog idempotency guard. Without it every check is a scan of all the
        # rows of the series — ~7 times per entry, up to 200 entries, UNDER THE GLOBAL write LOCK.
        Index("ix_samples_series_client_time", "account_id", "series_id",
              "client_captured_at"),
        Index("ix_samples_batch", "batch_id"),
        Index("ix_samples_time", "captured_at"),
    )


class SeriesState(Base):
    """The hot row per (account, series), which lets /api/status read a dozen-odd rows
    instead of a groupwise-max over the whole fact table. A cache — rebuildable from limit_samples."""
    __tablename__ = "series_state"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("usage_series.id"), primary_key=True)

    last_sample_id: Mapped[int | None] = mapped_column(BigInteger)
    last_captured_at: Mapped[datetime | None] = _dt()

    # Three different questions that one field cannot answer:
    #   last_captured_at  - when the last SAMPLE was written (dedup skips unchanged ones)
    #   last_confirmed_at - when it was last CONFIRMED that the value is still the same
    #   value_since       - since when the value has been unchanged
    # Without last_confirmed_at a stable reading looks exactly like a broken connection:
    # dedup writes no sample, so last_captured_at stands still and the UI reports "a measurement
    # from 6 minutes ago", even though the client confirmed that value 20 seconds ago.
    last_confirmed_at: Mapped[datetime | None] = _dt()
    value_since: Mapped[datetime | None] = _dt()

    last_utilization: Mapped[float | None] = mapped_column(Numeric(7, 4))
    last_resets_at: Mapped[datetime | None] = _dt()
    last_is_active: Mapped[bool | None] = mapped_column(Boolean)
    last_severity: Mapped[str | None] = mapped_column(String(32))
    last_extra: Mapped[dict | None] = mapped_column(JSON)

    prev_utilization: Mapped[float | None] = mapped_column(Numeric(7, 4))
    prev_captured_at: Mapped[datetime | None] = _dt()

    updated_at: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6),
                                       onupdate=func.now(6))


class IngestEvent(Base):
    __tablename__ = "ingest_events"

    id: Mapped[int] = mapped_column(PK_BIG, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("ingest_batches.id"))
    level: Mapped[str] = mapped_column(Enum("info", "warn", "error", name="event_level"),
                                       nullable=False, default="info")
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (
        Index("ix_events_ts", "ts"),
        Index("ix_events_type_ts", "event_type", "ts"),
        Index("ix_events_acct_ts", "account_id", "ts"),
    )


class AppSetting(Base):
    __tablename__ = "app_settings"

    setting_key: Mapped[str] = mapped_column(String(100), primary_key=True)
    setting_value: Mapped[str | None] = mapped_column(MEDIUMTEXT)
    updated_at: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6),
                                       onupdate=func.now(6))
