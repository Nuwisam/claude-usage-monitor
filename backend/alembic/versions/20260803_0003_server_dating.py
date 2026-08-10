"""Server-side dating of measurements: the client gives the age, the server dates.

Revision ID: 0003_server_dating
Revises: 0002_cli_source
Create Date: 2026-08-03

A probe measurement is made of TWO sources of different age — the `claude -p "/usage"` dump
and the Claude Code cache — but it rode on ONE timestamp, taken from the dump. `spend` and
`extra_usage` come exclusively from the cache, so they were rejuvenated by the whole age
difference (up to an hour). The backend decides by that timestamp which reading is current,
so with two machines on one account the one with the older cache but the fresher dump rolled
back the state of exactly those two series — the only ones that NEVER have a window boundary,
and therefore the only ones without a monotonicity guard.

From this version on the probe sends `measurement.sent_at` and `measurement.fresh_at`, and the
server dates `min(ts + (arrived_at - sent_at), arrived_at)`. The schema follows with two columns:

1. limit_samples.client_captured_at — the raw client time for THIS observation, before the
   offset. Forced by the backlog idempotency guard: `captured_at` is now a function of the
   REQUEST (it carries its offset), so a repeat of the same entry in a different request gets
   a different value and the guard would not see it. Plus an index — without it the check is a
   scan of all the series rows, ~7 times per entry, up to 200 entries, under a global write lock.

2. ingest_batches.clock_offset_s — the offset used for the request. Together with `received_at`
   it makes the computed `captured_at` reconstructible from the database.

THERE IS NO BACKFILL and there cannot be. Rows from before this revision come from probe v4,
which did not send `sent_at`; their `captured_at` is the raw client time, but rewriting it into
the new column would pretend those measurements went through the new path. The guard does not
look for them (it cares only about what arrived again), so NULL is the correct answer here.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0003_server_dating"
down_revision: Union[str, None] = "0002_cli_source"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DT = mysql.DATETIME(fsp=6)


def upgrade() -> None:
    op.add_column("limit_samples", sa.Column("client_captured_at", DT, nullable=True))
    op.create_index("ix_samples_series_client_time", "limit_samples",
                    ["account_id", "series_id", "client_captured_at"])
    op.add_column("ingest_batches", sa.Column("clock_offset_s", sa.Integer))


def downgrade() -> None:
    op.drop_column("ingest_batches", "clock_offset_s")
    op.drop_index("ix_samples_series_client_time", table_name="limit_samples")
    op.drop_column("limit_samples", "client_captured_at")
