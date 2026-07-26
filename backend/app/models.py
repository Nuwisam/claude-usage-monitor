"""Model danych.

Zasada naczelna: **otwarty zbior serii**. Krok 0 pokazal 17 kluczy najwyzszego poziomu
w odpowiedzi /api/oauth/usage, z czego 5 nie bylo znanych ani z walidatora w binarce
Claude Code, ani z repo referencyjnego (amber_ladder, iguana_necktie, nimbus_quill,
tangelo, omelette_promotional). Zahardkodowana lista bucketow bylaby bledna juz w dniu
napisania — dlatego serie sa wierszami w tabeli, a nie kolumnami.

Cztery zrodla serii, wszystkie sprowadzone do wspolnego "procent zuzycia 0-100":
  bucket       - klucz najwyzszego poziomu z polem `utilization`  (five_hour, seven_day, ...)
  limit        - wpis w `limits[]` z polem `percent`               (session, weekly_all, ...)
  extra_usage  - obiekt `extra_usage`, pole `utilization`
  spend        - obiekt `spend`, pole `percent`  <-- na koncie Team to JEST wiazacy limit
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


# Warianty zamiast typow z dialektu wprost — dzieki temu modele daja sie zaladowac
# takze pod SQLite (testy), a na MariaDB dostaja DATETIME(6) z mikrosekundami.
DT6 = DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")
LONGTEXT = Text().with_variant(mysql.LONGTEXT, "mysql")
MEDIUMTEXT = Text().with_variant(mysql.MEDIUMTEXT, "mysql")
# SQLite robi autoincrement wylacznie na INTEGER (alias rowid), nie na BIGINT.
# Na MariaDB zostaje BIGINT — tylko testy dostaja INTEGER.
PK_BIG = BigInteger().with_variant(Integer, "sqlite")


def _dt(**kw) -> Mapped[datetime]:
    return mapped_column(DT6, **kw)


# --------------------------------------------------------------------------- konta
class Account(Base):
    """Kluczem naturalnym jest account_uuid z oauthAccount, NIE label.

    Powod: na jednej maszynie przelaczasz sie miedzy kontami przez /login, a settings.json
    jest wspolny. Statyczny label w konfiguracji przypisywalby polowe probek do zlego konta
    i cicho zatruwal historie obu — bez zadnego widocznego objawu.
    """
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_uuid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    label: Mapped[str | None] = mapped_column(String(100))          # edytowalna nazwa wlasna
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
    """Maszyna == token ingestu. Sluzy do unieważnienia pojedynczej maszyny i do
    odpowiedzi na pytanie 'skad przyszly te dane'."""
    __tablename__ = "machines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    host: Mapped[str | None] = mapped_column(String(128))
    cc_version: Mapped[str | None] = mapped_column(String(32))
    script_version: Mapped[int | None] = mapped_column(Integer)
    first_seen_at: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6))
    last_seen_at: Mapped[datetime | None] = _dt()
    batches: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class MachineAccount(Base):
    """Ktora maszyna raportowala ktore konto. Zamiast zakazywac (statyczna lista kont per
    token by sie rozjechala, skoro na jednej maszynie uzywasz obu), wykrywamy: pierwsze
    wystapienie nowej pary generuje zdarzenie new_account_for_token."""
    __tablename__ = "machine_accounts"

    machine_id: Mapped[int] = mapped_column(ForeignKey("machines.id"), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    first_seen_at: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6))
    last_seen_at: Mapped[datetime | None] = _dt()
    samples: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


# --------------------------------------------------------------------------- serie
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
    # Seria widziana tylko jako null (np. seven_day_opus na koncie bez Opusa) — rejestrujemy,
    # ale oznaczamy, zeby UI nie zasmiecalo sie pustymi wykresami.
    ever_non_null: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# --------------------------------------------------------------------------- ingest
class RawPayload(Base):
    """Adresowane trescia. Sonda odpytuje co ~120 s, a odpowiedz przy bezczynnosci jest
    bajt-identyczna — hash zwija to do jednego wiersza i licznika. To dedup, NIE kompaktowanie:
    kazdy batch nadal wskazuje na swoje dokladne body."""
    __tablename__ = "raw_payloads"

    id: Mapped[int] = mapped_column(PK_BIG, primary_key=True, autoincrement=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    body: Mapped[str] = mapped_column(LONGTEXT, nullable=False)
    first_seen_at: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6))
    last_seen_at: Mapped[datetime | None] = _dt()
    seen_count: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)


class IngestBatch(Base):
    """Jeden wiersz na request. Kluczowe dla rozroznienia 'cisza klienta' od 'cisza w danych' —
    bez tego zepsuty klient wyglada identycznie jak brak aktywnosci, a UI pokazuje falszywe 0%."""
    __tablename__ = "ingest_batches"

    id: Mapped[int] = mapped_column(PK_BIG, primary_key=True, autoincrement=True)
    received_at: Mapped[datetime] = _dt(nullable=False, server_default=func.now(6))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    machine_id: Mapped[int | None] = mapped_column(ForeignKey("machines.id"))

    client_host: Mapped[str | None] = mapped_column(String(128))
    config_dir_hash: Mapped[str | None] = mapped_column(String(32))
    cc_version: Mapped[str | None] = mapped_column(String(32))
    script_version: Mapped[int | None] = mapped_column(Integer)
    hook_event: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(64))

    http_status: Mapped[int | None] = mapped_column(Integer)      # status od Anthropic
    request_id: Mapped[str | None] = mapped_column(String(64))
    rl_status: Mapped[str | None] = mapped_column(String(32))
    probe_ms: Mapped[int | None] = mapped_column(Integer)

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
    """Tabela faktow.

    ODSTEPSTWO OD PLANU, swiadome: plan zakladal klucz glowny zlozony i klastrujacy
    (account_id, series_id, captured_at, id) dla ciaglego range scanu. Przy realnym wolumenie —
    rzedu tysiecy wierszy dziennie, bo sonda ma throttle 120 s i dziala tylko gdy pracujesz —
    to przedwczesna optymalizacja, ktora komplikuje mapowanie ORM i AUTO_INCREMENT w InnoDB.
    Prosty PK + zlozony indeks daje ten sam plan zapytania przy tej skali.
    """
    __tablename__ = "limit_samples"

    id: Mapped[int] = mapped_column(PK_BIG, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    series_id: Mapped[int] = mapped_column(ForeignKey("usage_series.id"), nullable=False)
    captured_at: Mapped[datetime] = _dt(nullable=False)
    batch_id: Mapped[int] = mapped_column(ForeignKey("ingest_batches.id"), nullable=False)

    source: Mapped[str] = mapped_column(
        Enum("probe", "statusline", "ratelimit_headers", name="sample_source"),
        nullable=False, default="probe",
    )
    # ZAWSZE 0..100 po normalizacji. Skala zrodlowa roznila sie (naglowki daja 0.0-1.0),
    # dlatego kolumna `source` mowi skad przyszlo, a wartosc jest juz ujednolicona.
    utilization: Mapped[float | None] = mapped_column(Numeric(7, 4))
    resets_at: Mapped[datetime | None] = _dt()

    is_active: Mapped[bool | None] = mapped_column(Boolean)   # limits[].is_active
    severity: Mapped[str | None] = mapped_column(String(32))  # klasyfikacja od Anthropic
    stale_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    session_id: Mapped[str | None] = mapped_column(String(64))
    extra: Mapped[dict | None] = mapped_column(JSON)

    __table_args__ = (
        Index("ix_samples_series_time", "account_id", "series_id", "captured_at"),
        Index("ix_samples_batch", "batch_id"),
        Index("ix_samples_time", "captured_at"),
    )


class SeriesState(Base):
    """Goracy wiersz per (konto, seria). Dzieki niemu /api/status czyta kilkanascie wierszy
    zamiast robic groupwise-max po calej tabeli faktow. Cache — odtwarzalny z limit_samples."""
    __tablename__ = "series_state"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    series_id: Mapped[int] = mapped_column(ForeignKey("usage_series.id"), primary_key=True)

    last_sample_id: Mapped[int | None] = mapped_column(BigInteger)
    last_captured_at: Mapped[datetime | None] = _dt()
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
