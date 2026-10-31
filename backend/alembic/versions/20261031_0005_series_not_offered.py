"""A window the account does not have: series_state.not_offered_at.

Revision ID: 0005_series_not_offered
Revises: 0004_account_org_identity
Create Date: 2026-10-31

On 2026-10-31 Anthropic removed the 5-hour limit from the Ultra plan (formerly Max 20x). The
raw response has said `"five_hour": null` ever since, with no `kind=session` entry in
`limits[]`, and the series froze in `series_state` at its last sample: the cards showed a
day-old session percentage and the cascade marked that frozen rung as the current one.

The column holds the newest reading whose raw response says this account has no such window.
NULL means offered, or no information. It is per account, not on `usage_series`: that row is
global, and the same window exists on one account and not on another.

THERE IS NO BACKFILL. Which batches said "not offered" is knowable only by re-reading
raw_payloads, and the first reading after deploy says it anyway — each account's frozen series
disappear on that account's first batch.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0005_series_not_offered"
down_revision: Union[str, None] = "0004_account_org_identity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DT = mysql.DATETIME(fsp=6)


def upgrade() -> None:
    op.add_column("series_state", sa.Column("not_offered_at", DT, nullable=True))


def downgrade() -> None:
    op.drop_column("series_state", "not_offered_at")
