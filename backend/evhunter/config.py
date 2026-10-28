from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True)
    app_env: Literal["development", "test", "production"] = "development"
    data_mode: Literal["demo", "live"] = "demo"
    database_url: str = f"sqlite:///{ROOT / 'data' / 'evhunter.db'}"
    redis_url: str = ""
    session_secret: str = "local-demo-secret-change-before-deploy-2026"
    owner_username: str = "daniel"
    owner_password: str = "evhunter-demo-2026"
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    embed_worker: bool = True
    odds_api_key: str = Field(default="", repr=False)
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"
    sports: str = "basketball_nba,americanfootball_nfl,baseball_mlb,icehockey_nhl"
    bookmakers: str = "fanduel,draftkings"
    reviewed_books: str = ""
    sport_rule_reviews: dict[str, list[str]] = Field(default_factory=dict)
    exchange_fee_config: str = "{}"
    poll_seconds: int = Field(default=60, ge=20)
    prediction_poll_seconds: int = Field(default=20, ge=10)
    monthly_credit_budget: int = Field(default=450, ge=0)
    kalshi_base_url: str = "https://external-api.kalshi.com/trade-api/v2"
    polymarket_base_url: str = "https://clob.polymarket.com"
    polymarket_us_base_url: str = "https://gateway.polymarket.us"
    prediction_streams: str = "auto"
    kalshi_api_key_id: str = Field(default="", repr=False)
    kalshi_private_key_path: str = Field(default="", repr=False)
    kalshi_private_key: str = Field(default="", repr=False)
    polymarket_us_key_id: str = Field(default="", repr=False, validation_alias=AliasChoices("polymarket_us_key_id", "POLYMARKET_KEY_ID"))
    polymarket_us_secret_key: str = Field(default="", repr=False, validation_alias=AliasChoices("polymarket_us_secret_key", "POLYMARKET_SECRET_KEY"))
    novig_key_id: str = Field(default="", repr=False)
    novig_private_key_path: str = Field(default="", repr=False)
    novig_private_key: str = Field(default="", repr=False)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    history_retention_days: int = Field(default=30, ge=1, le=365)

    @field_validator("bookmakers")
    @classmethod
    def free_sportsbooks(cls, value):
        # Also repair old .env files that still contain the former default.
        books = list(dict.fromkeys(b.strip() for b in value.split(",") if b.strip() and b.strip() != "fanatics"))
        if not books:
            raise ValueError("Configure at least one sportsbook; Fanatics is no longer part of this feed")
        return ",".join(books)

    @field_validator("prediction_streams")
    @classmethod
    def validate_streams(cls, value):
        value = ",".join(v.strip().lower() for v in value.split(",") if v.strip())
        if value not in {"", "auto", "off", "none"} and set(value.split(",")) - {"kalshi", "polymarket", "polymarket_us", "novig"}:
            raise ValueError("Unsupported prediction stream; use auto, off, or comma-separated venue names")
        return value

    @property
    def stream_sources(self) -> list[str]:
        if self.prediction_streams in {"off", "none"}:
            return []
        if self.prediction_streams in {"", "auto"}:
            # Public international market data needs no trading key. Streams
            # are only opened after a matching reviewed contract is present.
            sources = ["polymarket"]
            if self.kalshi_api_key_id and (self.kalshi_private_key_path or self.kalshi_private_key):
                sources.append("kalshi")
            if self.novig_key_id and (self.novig_private_key_path or self.novig_private_key):
                sources.append("novig")
            if self.polymarket_us_key_id and self.polymarket_us_secret_key:
                sources.append("polymarket_us")
            return sources
        sources = [v.strip() for v in self.prediction_streams.split(",") if v.strip()]
        if set(sources) - {"kalshi", "polymarket", "polymarket_us", "novig"}:
            raise ValueError("Unsupported prediction stream")
        return list(dict.fromkeys(sources))

    @property
    def origins(self) -> list[str]:
        return [s.strip().rstrip("/") for s in self.allowed_origins.split(",") if s.strip()]

    @model_validator(mode="after")
    def production_requirements(self):
        if self.app_env == "production":
            if len(self.session_secret) < 32 or self.session_secret.startswith("local-demo"):
                raise ValueError("Set a unique SESSION_SECRET of at least 32 characters")
            if len(self.owner_password) < 12 or self.owner_password == "evhunter-demo-2026":
                raise ValueError("Set a unique OWNER_PASSWORD of at least 12 characters")
            if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
                raise ValueError("Production requires PostgreSQL")
            if not self.redis_url:
                raise ValueError("Production requires REDIS_URL")
            if any(not o.startswith("https://") for o in self.origins):
                raise ValueError("Production ALLOWED_ORIGINS must use HTTPS")
        return self
