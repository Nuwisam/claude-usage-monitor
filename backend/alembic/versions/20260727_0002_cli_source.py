"""Zrodlo pomiaru: CLI zamiast wlasnego zadania HTTP; rozdzielenie swiezosci od zmiany.

Revision ID: 0002_cli_source
Revises: 0001_initial
Create Date: 2026-07-27

Sonda przestala wolac https://api.anthropic.com/api/oauth/usage wlasnym tokenem. Pomiar
zleca teraz Claude Code (`claude -p "/usage"`), a sonda czyta wynik z dysku. Migracja
sciaga za tym schemat w trzech krokach:

1. DODAJE series_state.last_confirmed_at i series_state.value_since.
   Jedno pole nie moze odpowiadac na dwa pytania. Dedup celowo nie zapisuje probki, gdy
   wartosc sie nie zmienila, wiec last_captured_at bywa o minuty starszy niz ostatni realny
   pomiar. Liczenie swiezosci z niego mylilo "nic sie nie zmienia" z "stracilismy lacznosc"
   — a to sa przeciwne informacje dla kogos, kto wlasnie decyduje o duzym zadaniu.
   Backfill: dla istniejacych wierszy oba znaczenia sa tozsame, wiec kopiujemy
   last_captured_at.

2. USUWA kolumny, ktore opisywaly nieistniejacy juz byt — zadanie HTTP sondy do Anthropic:
   ingest_batches.http_status / request_id / rl_status.
   Oraz cc_version z machines i ingest_batches: pochodzila z UA_VERSION, czyli ze stalej
   "2.1.215" zaszytej w kodzie sondy. Kolumna trzymala te sama wartosc dla kazdej maszyny
   i nie mowila nic o wersji tam dzialajacej — byla gorsza niz brak kolumny, bo wygladala
   na dane.

3. PRZESTAWIA enum limit_samples.source. `probe` zostaje, bo historyczne wiersze sa
   poprawnymi pomiarami. `statusline` i `ratelimit_headers` wypadaja: nigdy nie zostaly
   wdrozone i nigdy nie zostana — statusline nie dziala w rozszerzeniu VS Code (#55643,
   zamkniete not_planned), a naglowki anthropic-ratelimit-unified-* wymagalyby proxy MITM
   na wlasnym ruchu. Enum obiecywal zrodla, ktorych nie ma.
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
    # --- 1. swiezosc vs zmiana wartosci -------------------------------------------
    op.add_column("series_state", sa.Column("last_confirmed_at", DT, nullable=True))
    op.add_column("series_state", sa.Column("value_since", DT, nullable=True))
    # Dla danych sprzed tej zmiany oba znaczenia sa tozsame — nie ma z czego odtworzyc
    # momentu, w ktorym wartosc faktycznie sie ustalila, wiec bierzemy to co wiemy.
    op.execute("UPDATE series_state SET last_confirmed_at = last_captured_at, "
               "value_since = last_captured_at")

    # --- 2. kolumny po nieistniejacym zadaniu HTTP ---------------------------------
    op.add_column("ingest_batches", sa.Column("measurement_source", sa.String(32)))
    op.add_column("ingest_batches", sa.Column("cache_age_s", sa.Integer))
    op.add_column("ingest_batches", sa.Column("fresh_age_s", sa.Integer))
    # Historyczne batche pochodza z sondy v2, ktora sama odpytywala endpoint.
    op.execute("UPDATE ingest_batches SET measurement_source = 'probe'")

    op.drop_column("ingest_batches", "http_status")
    op.drop_column("ingest_batches", "request_id")
    op.drop_column("ingest_batches", "rl_status")
    op.drop_column("ingest_batches", "cc_version")
    op.drop_column("machines", "cc_version")

    # --- 3. enum zrodla probki -----------------------------------------------------
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
    # Danych z tych kolumn nie da sie odtworzyc — powrot przywraca ksztalt, nie tresc.

    op.drop_column("ingest_batches", "fresh_age_s")
    op.drop_column("ingest_batches", "cache_age_s")
    op.drop_column("ingest_batches", "measurement_source")

    op.drop_column("series_state", "value_since")
    op.drop_column("series_state", "last_confirmed_at")
