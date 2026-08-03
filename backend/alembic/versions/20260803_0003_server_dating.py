"""Datowanie pomiarow po stronie serwera: klient podaje wiek, serwer datuje.

Revision ID: 0003_server_dating
Revises: 0002_cli_source
Create Date: 2026-08-03

Pomiar sondy sklada sie z DWOCH zrodel o roznym wieku — zrzutu `claude -p "/usage"`
i cache'u Claude Code — a jechal na JEDNYM stemplu, wzietym ze zrzutu. `spend` i
`extra_usage` pochodza wylacznie z cache, wiec byly odmladzane o cala roznice wiekow
(do godziny). Backend rozstrzyga po tym stemplu, ktory odczyt jest biezacy, wiec przy
dwoch maszynach na jednym koncie ta ze starszym cache'em, ale swiezszym zrzutem cofala
stan wlasnie tych dwoch serii — jedynych, ktore granicy okna nie maja NIGDY, a wiec
jedynych bez guardu monotonicznosci.

Od tej wersji sonda wysyla `measurement.sent_at` i `measurement.fresh_at`, a serwer datuje
`min(ts + (arrived_at - sent_at), arrived_at)`. Schemat sciaga za tym dwie kolumny:

1. limit_samples.client_captured_at — surowy czas klienta dla TEJ obserwacji, przed
   offsetem. Wymuszona przez guard idempotencji backlogu: `captured_at` jest teraz funkcja
   ZADANIA (zawiera jego offset), wiec powtorka tego samego wpisu w innym zadaniu dostaje
   inna wartosc i guard by jej nie zobaczyl. Plus indeks — bez niego sprawdzenie to skan
   wszystkich wierszy serii, ~7 razy na wpis, do 200 wpisow, pod globalnym lockiem zapisu.

2. ingest_batches.clock_offset_s — offset uzyty dla zadania. Razem z `received_at`
   czyni wyliczony `captured_at` odtwarzalnym z bazy.

BACKFILLU NIE MA i nie moze byc. Wiersze sprzed tej rewizji pochodza od sondy v4, ktora
`sent_at` nie wysylala; ich `captured_at` jest surowym czasem klienta, ale przepisanie go
do nowej kolumny udawaloby, ze te pomiary przeszly nowa sciezka. Guard ich nie szuka
(interesuje go tylko to, co przyszlo ponownie), wiec NULL jest tu poprawna odpowiedzia.
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
