from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import JSON, DateTime, Float, Index, Integer, Numeric, String, Text, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from .config import ROOT, Config
from .domain import DEFAULT_EXECUTION_BOOKS, utcnow
from .sports import DEFAULT_LIVE_SPORTS, LEGACY_RULE_SPORTS, SPORTS, SPORT_PROFILES


class Base(DeclarativeBase):
    pass


class Preference(Base):
    __tablename__ = "preferences"
    mode: Mapped[str] = mapped_column(String(8), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class CurrentQuote(Base):
    __tablename__ = "current_quotes"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    market_id: Mapped[str] = mapped_column(String(32), index=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(JSON)


class History(Base):
    __tablename__ = "quote_history"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mode: Mapped[str] = mapped_column(String(8))
    market_id: Mapped[str] = mapped_column(String(32))
    book: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(120))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    odds: Mapped[float] = mapped_column(Float)
    __table_args__ = (Index("history_market_time", "mode", "market_id", "at"),)


class OpportunityRecord(Base):
    __tablename__ = "opportunities"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Bet(Base):
    __tablename__ = "bets"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="open")
    stake: Mapped[float] = mapped_column(Numeric(18, 2, asdecimal=False))
    odds: Mapped[float] = mapped_column(Float)
    profit: Mapped[float] = mapped_column(Numeric(18, 2, asdecimal=False), default=0)
    data: Mapped[dict] = mapped_column(JSON)


class Ledger(Base):
    __tablename__ = "bankroll_ledger"
    id: Mapped[str] = mapped_column(String(50), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    amount: Mapped[float] = mapped_column(Numeric(18, 2, asdecimal=False))
    kind: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(String(200))


class SourceState(Base):
    __tablename__ = "source_states"
    id: Mapped[str] = mapped_column(String(60), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)


class MarketMapping(Base):
    __tablename__ = "market_mappings"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    data: Mapped[dict] = mapped_column(JSON)


class BetResearch(Base):
    __tablename__ = "bet_research"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    market_id: Mapped[str] = mapped_column(String(32), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(JSON)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    mode: Mapped[str] = mapped_column(String(8), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(JSON)
    read: Mapped[int] = mapped_column(Integer, default=0)
    delivery: Mapped[str] = mapped_column(String(20), default="in_app")


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    action: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text)


def make_engine(config: Config):
    url = config.database_url
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url[len("postgres://"):]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("sqlite"):
        (ROOT / "data").mkdir(exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False}, pool_pre_ping=True)
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)
    return engine


def default_preferences(mode: str) -> dict:
    return {
        "min_ev": 1.0, "min_arb": 0.2, "devig_method": "proportional",
        "reference_book": "draftkings", "kelly_fraction": 0.25,
        "max_stake_pct": 2.0, "arb_budget": 1000.0,
        "max_quote_age": 120, "max_quote_skew": 75,
        "jurisdiction": "NY", "allowed_books": list(DEFAULT_EXECUTION_BOOKS),
        "alert_ev": 3.0, "alert_arb": 1.0, "email": "", "email_alerts": False,
        "venue_balances": {}, "jurisdiction_reviewed_at": None,
        "enabled_sports": list(SPORT_PROFILES) if mode == "demo" else list(DEFAULT_LIVE_SPORTS),
        "league_rule_reviews": {s: list(DEFAULT_EXECUTION_BOOKS) for s in SPORT_PROFILES} if mode == "demo" else {},
    }


def initialize(engine, config: Config):
    # Serialize first boot when Railway starts the API and worker concurrently.
    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(71423001)"))
        Base.metadata.create_all(connection)
        with Session(bind=connection) as session:
            initial_sports = list(SPORT_PROFILES) if config.data_mode == "demo" else list(dict.fromkeys(SPORTS[k.strip()] for k in config.sports.split(",") if k.strip() in SPORTS))
            initial_reviews = {s: list(DEFAULT_EXECUTION_BOOKS) for s in SPORT_PROFILES} if config.data_mode == "demo" else {
                s: config.sport_rule_reviews.get(s, [b.strip() for b in config.reviewed_books.split(",") if b.strip()] if s in LEGACY_RULE_SPORTS else []) for s in SPORT_PROFILES}
            if not session.get(Preference, config.data_mode):
                session.add(Preference(mode=config.data_mode, data={**default_preferences(config.data_mode), "enabled_sports": initial_sports, "league_rule_reviews": initial_reviews}))
            # Apply the requested venue restriction once, preserving all bets, cash and other settings.
            migration_id = f"{config.data_mode}:three-book-defaults-v1"
            if not session.get(SourceState, migration_id):
                preference = session.get(Preference, config.data_mode)
                if preference:
                    preference.data = {**preference.data, "allowed_books": list(DEFAULT_EXECUTION_BOOKS), "reference_book": "draftkings"}
                session.add(SourceState(id=migration_id, data={"applied_at": utcnow().isoformat()}))
            preference = session.get(Preference, config.data_mode)
            preference.data = {**preference.data, "enabled_sports": preference.data.get("enabled_sports", initial_sports), "league_rule_reviews": preference.data.get("league_rule_reviews", initial_reviews)}
            if not session.get(Ledger, f"{config.data_mode}:opening"):
                session.add(Ledger(id=f"{config.data_mode}:opening", mode=config.data_mode, at=utcnow()-timedelta(days=30) if config.data_mode == "demo" else utcnow(), amount=10000 if config.data_mode == "demo" else 0, kind="deposit", note="Synthetic opening bankroll" if config.data_mode == "demo" else "Opening balance"))
            session.flush()


@contextmanager
def transaction(engine):
    with Session(engine) as session:
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
