"""Initial schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-07-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DT = mysql.DATETIME(fsp=6)


def _dt(name, nullable=True, default_now=False):
    kw = {}
    if default_now:
        kw["server_default"] = sa.text("CURRENT_TIMESTAMP(6)")
    return sa.Column(name, DT, nullable=nullable, **kw)


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        # The natural key is the account UUID, NOT the label from configuration — because on
        # one machine accounts are switched via /login and a static label would go out of sync.
        sa.Column("account_uuid", sa.String(64), nullable=False, unique=True),
        sa.Column("label", sa.String(100)),
        sa.Column("email", sa.String(255)),
        sa.Column("display_name", sa.String(255)),
        sa.Column("color", sa.String(16)),
        sa.Column("org_uuid", sa.String(64)),
        sa.Column("org_name", sa.String(255)),
        sa.Column("org_type", sa.String(64)),
        sa.Column("seat_tier", sa.String(32)),
        sa.Column("org_rate_limit_tier", sa.String(64)),
        sa.Column("user_rate_limit_tier", sa.String(64)),
        sa.Column("subscription_type", sa.String(32)),
        sa.Column("extra_usage_enabled", sa.Boolean),
        sa.Column("is_enabled", sa.Boolean, nullable=False, server_default=sa.text("1")),
        _dt("first_seen_at", nullable=False, default_now=True),
        _dt("last_sample_at"),
        sa.Column("last_client_host", sa.String(128)),
        _dt("created_at", nullable=False, default_now=True),
        _dt("updated_at", nullable=False, default_now=True),
        _dt("archived_at"),
        mysql_row_format="DYNAMIC",
    )

    op.create_table(
        "machines",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("host", sa.String(128)),
        sa.Column("cc_version", sa.String(32)),
        sa.Column("script_version", sa.Integer),
        _dt("first_seen_at", nullable=False, default_now=True),
        _dt("last_seen_at"),
        sa.Column("batches", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    )

    op.create_table(
        "machine_accounts",
        sa.Column("machine_id", sa.Integer, sa.ForeignKey("machines.id"), primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), primary_key=True),
        _dt("first_seen_at", nullable=False, default_now=True),
        _dt("last_seen_at"),
        sa.Column("samples", sa.BigInteger, nullable=False, server_default=sa.text("0")),
    )

    # An open set of series. Step 0 showed 17 top-level keys, 5 of them unknown —
    # a new bucket at Anthropic should add a row, not require a migration.
    op.create_table(
        "usage_series",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("series_key", sa.String(255), nullable=False, unique=True),
        sa.Column("source", sa.Enum("bucket", "limit", "extra_usage", "spend",
                                    name="series_source"), nullable=False),
        sa.Column("bucket_key", sa.String(100)),
        sa.Column("kind", sa.String(100)),
        sa.Column("group_key", sa.String(100)),
        sa.Column("model_display_name", sa.String(100)),
        sa.Column("surface_display_name", sa.String(100)),
        sa.Column("display_label", sa.String(200), nullable=False),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default=sa.text("1000")),
        _dt("first_seen_at", nullable=False, default_now=True),
        _dt("last_seen_at"),
        sa.Column("ever_non_null", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.text("1")),
        mysql_row_format="DYNAMIC",
    )

    # Content-addressed: when idle the response is byte-identical.
    op.create_table(
        "raw_payloads",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("body", mysql.LONGTEXT, nullable=False),
        _dt("first_seen_at", nullable=False, default_now=True),
        _dt("last_seen_at"),
        sa.Column("seen_count", sa.BigInteger, nullable=False, server_default=sa.text("1")),
        mysql_row_format="COMPRESSED",
    )

    # One row per request. Key to telling "client silence" from "silence in the data" —
    # without it a broken probe looks like no activity and the UI shows a false 0%.
    op.create_table(
        "ingest_batches",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _dt("received_at", nullable=False, default_now=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id")),
        sa.Column("machine_id", sa.Integer, sa.ForeignKey("machines.id")),
        sa.Column("client_host", sa.String(128)),
        sa.Column("config_dir_hash", sa.String(32)),
        sa.Column("cc_version", sa.String(32)),
        sa.Column("script_version", sa.Integer),
        sa.Column("hook_event", sa.String(64)),
        sa.Column("session_id", sa.String(64)),
        sa.Column("http_status", sa.Integer),
        sa.Column("request_id", sa.String(64)),
        sa.Column("rl_status", sa.String(32)),
        sa.Column("probe_ms", sa.Integer),
        sa.Column("raw_payload_id", sa.BigInteger, sa.ForeignKey("raw_payloads.id")),
        sa.Column("payload_sha256", sa.String(64)),
        sa.Column("samples_written", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("backlog_size", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("ok", sa.Boolean, nullable=False, server_default=sa.text("1")),
        sa.Column("error_kind", sa.String(32)),
        sa.Column("error_message", sa.Text),
        mysql_row_format="DYNAMIC",
    )
    op.create_index("ix_batches_acct_time", "ingest_batches", ["account_id", "received_at"])
    op.create_index("ix_batches_time", "ingest_batches", ["received_at"])

    op.create_table(
        "limit_samples",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("series_id", sa.Integer, sa.ForeignKey("usage_series.id"), nullable=False),
        _dt("captured_at", nullable=False),
        sa.Column("batch_id", sa.BigInteger, sa.ForeignKey("ingest_batches.id"), nullable=False),
        sa.Column("source", sa.Enum("probe", "statusline", "ratelimit_headers",
                                    name="sample_source"),
                  nullable=False, server_default="probe"),
        # ALWAYS 0..100 after normalization; `source` says which scale it came from.
        sa.Column("utilization", sa.Numeric(7, 4)),
        _dt("resets_at"),
        sa.Column("is_active", sa.Boolean),
        sa.Column("severity", sa.String(32)),
        sa.Column("stale_read", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("session_id", sa.String(64)),
        sa.Column("extra", mysql.JSON),
        mysql_row_format="DYNAMIC",
    )
    op.create_index("ix_samples_series_time", "limit_samples",
                    ["account_id", "series_id", "captured_at"])
    op.create_index("ix_samples_batch", "limit_samples", ["batch_id"])
    op.create_index("ix_samples_time", "limit_samples", ["captured_at"])

    # Cache of the hot state, so /api/status reads a dozen or so rows.
    op.create_table(
        "series_state",
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), primary_key=True),
        sa.Column("series_id", sa.Integer, sa.ForeignKey("usage_series.id"), primary_key=True),
        sa.Column("last_sample_id", sa.BigInteger),
        _dt("last_captured_at"),
        sa.Column("last_utilization", sa.Numeric(7, 4)),
        _dt("last_resets_at"),
        sa.Column("last_is_active", sa.Boolean),
        sa.Column("last_severity", sa.String(32)),
        sa.Column("last_extra", mysql.JSON),
        sa.Column("prev_utilization", sa.Numeric(7, 4)),
        _dt("prev_captured_at"),
        _dt("updated_at", nullable=False, default_now=True),
        mysql_row_format="DYNAMIC",
    )

    op.create_table(
        "ingest_events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        _dt("ts", nullable=False, default_now=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id")),
        sa.Column("batch_id", sa.BigInteger, sa.ForeignKey("ingest_batches.id")),
        sa.Column("level", sa.Enum("info", "warn", "error", name="event_level"),
                  nullable=False, server_default="info"),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("message", sa.Text),
        sa.Column("detail", mysql.JSON),
        mysql_row_format="DYNAMIC",
    )
    op.create_index("ix_events_ts", "ingest_events", ["ts"])
    op.create_index("ix_events_type_ts", "ingest_events", ["event_type", "ts"])
    op.create_index("ix_events_acct_ts", "ingest_events", ["account_id", "ts"])

    op.create_table(
        "app_settings",
        sa.Column("setting_key", sa.String(100), primary_key=True),
        sa.Column("setting_value", mysql.MEDIUMTEXT),
        _dt("updated_at", nullable=False, default_now=True),
    )


def downgrade() -> None:
    for t in ("app_settings", "ingest_events", "series_state", "limit_samples",
              "ingest_batches", "raw_payloads", "usage_series", "machine_accounts",
              "machines", "accounts"):
        op.drop_table(t)
