"""Measurement source: the CLI instead of its own HTTP request; freshness split from change.

Revision ID: 0002_cli_source
Revises: 0001_initial
Create Date: 2026-07-27

The probe stopped calling https://api.anthropic.com/api/oauth/usage with its own token. The
measurement is now delegated to Claude Code (`claude -p "/usage"`), and the probe reads the
result from disk. The migration pulls the schema along in three steps:

1. ADDS series_state.last_confirmed_at and series_state.value_since.
   One field cannot answer two questions. Dedup deliberately does not store a sample when the
   value has not changed, so last_captured_at is sometimes minutes older than the last real
   measurement. Computing freshness from it confused "nothing is changing" with "contact was
   lost" — and those are opposite pieces of information for someone deciding on a large job.
   Backfill: for existing rows both meanings are identical, so last_captured_at is
   copied.

2. REMOVES columns that described an entity that no longer exists — the probe's HTTP request
   to Anthropic: ingest_batches.http_status / request_id / rl_status.
   And cc_version from machines and ingest_batches: it came from UA_VERSION, that is from the
   constant "2.1.215" baked into the probe's code. The column held the same value for every
   machine and said nothing about the version running there — it was worse than no column,
   because it looked like data.

3. SWITCHES OVER the limit_samples.source enum. `probe` stays, because the historical rows
   are correct measurements. `statusline` and `ratelimit_headers` drop out: they were never
   deployed and never will be — statusline does not work in the VS Code extension (#55643,
   closed not_planned), and the anthropic-ratelimit-unified-* headers would require a MITM
   proxy on one's own traffic. The enum promised sources that do not exist.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0002_cli_source"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DT = mysql.DATETIME(fsp=6)

SOURCE_NEW = sa.Enum("probe", "cli_merged", "cli_usage_cache", name="sample_source")
SOURCE_OLD = sa.Enum("probe", "statusline", "ratelimit_headers", name="sample_source")


def upgrade() -> None:
    # --- 1. freshness vs change of value ------------------------------------------
    op.add_column("series_state", sa.Column("last_confirmed_at", DT, nullable=True))
    op.add_column("series_state", sa.Column("value_since", DT, nullable=True))
    # For data from before this change both meanings are identical — nothing in the old schema
    # records the moment the value actually settled, so the best available value is used.
    op.execute("UPDATE series_state SET last_confirmed_at = last_captured_at, "
               "value_since = last_captured_at")

    # --- 2. columns left over from the non-existent HTTP request -------------------
    op.add_column("ingest_batches", sa.Column("measurement_source", sa.String(32)))
    op.add_column("ingest_batches", sa.Column("cache_age_s", sa.Integer))
    op.add_column("ingest_batches", sa.Column("fresh_age_s", sa.Integer))
    # Historical batches come from probe v2, which polled the endpoint itself.
    op.execute("UPDATE ingest_batches SET measurement_source = 'probe'")

    op.drop_column("ingest_batches", "http_status")
    op.drop_column("ingest_batches", "request_id")
    op.drop_column("ingest_batches", "rl_status")
    op.drop_column("ingest_batches", "cc_version")
    op.drop_column("machines", "cc_version")

    # --- 3. sample source enum -----------------------------------------------------
    op.alter_column("limit_samples", "source", existing_type=SOURCE_OLD,
                    type_=SOURCE_NEW, nullable=False, server_default=None)


def downgrade() -> None:
    op.alter_column("limit_samples", "source", existing_type=SOURCE_NEW,
                    type_=SOURCE_OLD, nullable=False, server_default=None)

    op.add_column("machines", sa.Column("cc_version", sa.String(32)))
    op.add_column("ingest_batches", sa.Column("cc_version", sa.String(32)))
    op.add_column("ingest_batches", sa.Column("rl_status", sa.String(32)))
    op.add_column("ingest_batches", sa.Column("request_id", sa.String(64)))
    op.add_column("ingest_batches", sa.Column("http_status", sa.Integer))
    # The data from these columns cannot be recovered — the downgrade restores shape, not content.

    op.drop_column("ingest_batches", "fresh_age_s")
    op.drop_column("ingest_batches", "cache_age_s")
    op.drop_column("ingest_batches", "measurement_source")

    op.drop_column("series_state", "value_since")
    op.drop_column("series_state", "last_confirmed_at")
