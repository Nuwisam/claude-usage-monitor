"""Account identity is the pair (account_uuid, org_uuid), not account_uuid alone.

Revision ID: 0004_account_org_identity
Revises: 0003_server_dating
Create Date: 2026-10-16

One Claude user can sit in several organizations — a Team seat and a private Max plan — under
ONE oauthAccount.accountUuid. `get_or_create_account` looked the row up by account_uuid alone
and overwrote org_uuid, org_name, org_type, seat_tier and the rate-limit tiers from whichever
payload came last, so the measurements of BOTH organizations landed on one row and its plan
fields flipped with every switch. 40% of a Team seat and 40% of Max are different amounts, so
one row for both is wrong data, not a cosmetic glitch.

Three steps, in this order:

1. org_uuid NULL -> '' backfill, then NOT NULL DEFAULT ''. A payload without an organization is
   an identity of its own, and '' is a VALUE: MariaDB treats NULLs in a UNIQUE key as distinct,
   so (uuid, NULL) twice would both be accepted and the key would protect nothing.
2. Drop the single-column unique key. 0001 declared it with `unique=True`, that is unnamed, and
   MariaDB named the index after the column: UNIQUE KEY `account_uuid`.
3. UNIQUE (account_uuid, org_uuid), named, so downgrade and SHOW CREATE TABLE agree.

THERE IS NO DATA SPLIT HERE and there cannot be one. Which batches belong to which organization
is not in the schema — raw_payloads stores the `usage` object, not the account block — so it is
knowable only from one database's own `plan_changed` timeline. Separating rows that were already
merged is an operator's job on that database, not a migration every install runs.

DOWNGRADE REFUSES BY DESIGN once two rows share an account_uuid: merging two organizations'
samples back into one row is a data decision, never a migration. It checks before touching
anything, because MariaDB DDL does not roll back — see `downgrade`. The way back from such a
database is the backup taken before this revision.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_account_org_identity"
down_revision: Union[str, None] = "0003_server_dating"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE accounts SET org_uuid = '' WHERE org_uuid IS NULL")
    # `existing_type` is REQUIRED on MySQL/MariaDB: alembic renders MODIFY COLUMN with the
    # full column definition, and without the type there is nothing to render.
    op.alter_column("accounts", "org_uuid", existing_type=sa.String(64),
                    nullable=False, server_default="")
    # A UNIQUE constraint IS an index on MySQL/MariaDB; drop_constraint(type_="unique") would
    # be rendered as DROP INDEX anyway. drop_index names what actually happens.
    op.drop_index("account_uuid", table_name="accounts")
    op.create_unique_constraint("uq_accounts_identity", "accounts",
                                ["account_uuid", "org_uuid"])


def downgrade() -> None:
    # Refuse BEFORE the first DDL statement. MariaDB commits every DDL statement on its own,
    # so letting the unique-index creation below be the step that fails would leave the
    # composite key already dropped: `accounts` with no unique key at all, and the revision
    # still at 0004. Two rows sharing an account_uuid cannot be represented by the old
    # schema; the way back is the backup taken before the upgrade.
    shared = op.get_bind().execute(sa.text(
        "SELECT account_uuid FROM accounts GROUP BY account_uuid HAVING COUNT(*) > 1"
    )).scalars().all()
    if shared:
        raise RuntimeError(
            "downgrade 0004 refused: %d account_uuid(s) belong to more than one organization "
            "(%s). The pre-0004 schema cannot hold them; restore the backup taken before "
            "the upgrade instead." % (len(shared), ", ".join(shared)))
    op.drop_constraint("uq_accounts_identity", "accounts", type_="unique")
    op.create_index("account_uuid", "accounts", ["account_uuid"], unique=True)
    op.alter_column("accounts", "org_uuid", existing_type=sa.String(64),
                    nullable=True, server_default=None, existing_server_default="")
    op.execute("UPDATE accounts SET org_uuid = NULL WHERE org_uuid = ''")
