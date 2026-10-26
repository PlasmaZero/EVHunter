from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
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
    odds_api_key: str = ""
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"
    sports: str = "basketball_nba,americanfootball_nfl"
    bookmakers: str = "fanduel,fanatics,draftkings"
    reviewed_books: str = ""
    exchange_fee_config: str = "{}"
    poll_seconds: int = Field(default=60, ge=20)
    prediction_poll_seconds: int = Field(default=20, ge=10)
    monthly_credit_budget: int = Field(default=4_500_000, ge=0)
    kalshi_base_url: str = "https://external-api.kalshi.com/trade-api/v2"
    polymarket_base_url: str = "https://clob.polymarket.com"
    polymarket_us_base_url: str = "https://gateway.polymarket.us"
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_starttls: bool = True
    smtp_ssl: bool = False
    history_retention_days: int = Field(default=30, ge=1, le=365)

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
