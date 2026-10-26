import hashlib
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

BOOKS = {
    "fanduel": {"name": "FanDuel", "short": "FD", "color": "#2998ff", "kind": "Sportsbook"},
    "draftkings": {"name": "DraftKings", "short": "DK", "color": "#8bcc38", "kind": "Sportsbook"},
    "betmgm": {"name": "BetMGM", "short": "MG", "color": "#c2a46f", "kind": "Sportsbook"},
    "fanatics": {"name": "Fanatics", "short": "FA", "color": "#ed424a", "kind": "Sportsbook"},
    "williamhill_us": {"name": "Caesars", "short": "CZ", "color": "#cdb781", "kind": "Sportsbook"},
    "novig": {"name": "Novig", "short": "NV", "color": "#b5f256", "kind": "Exchange"},
    "prophetx": {"name": "ProphetX", "short": "PX", "color": "#b496ff", "kind": "Exchange"},
    "kalshi": {"name": "Kalshi", "short": "KA", "color": "#38d890", "kind": "Prediction market"},
    "polymarket_us": {"name": "Polymarket US", "short": "PU", "color": "#5488ff", "kind": "Prediction market"},
    "polymarket": {"name": "Polymarket Intl.", "short": "PM", "color": "#768cff", "kind": "Prediction market"},
    "pinnacle": {"name": "Pinnacle", "short": "PN", "color": "#f19051", "kind": "Reference"},
}
EXECUTION_BOOKS = [b for b in BOOKS if b != "pinnacle" and b != "polymarket"]
DEFAULT_EXECUTION_BOOKS = ["fanduel", "fanatics", "draftkings"]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def stable_id(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


class Quote(BaseModel):
    event_id: str
    sport: str
    home: str
    away: str
    starts_at: datetime
    market: Literal["moneyline", "spread", "total"]
    line: float | None = None  # For spreads this is ALWAYS the home team's line.
    ruleset: str
    outcome: str
    outcomes: list[str]
    selection: str
    book: str
    odds: float = Field(gt=1, le=10000)
    source_at: datetime
    received_at: datetime
    timestamp_basis: Literal["source", "snapshot"] = "source"
    max_stake: float | None = Field(default=None, gt=0)
    contracts: float | None = Field(default=None, gt=0)
    share_price: float | None = Field(default=None, gt=0, lt=1)
    fee_model: Literal["none", "profit", "curve", "per_contract", "unknown"] = "none"
    fee_rate: float = Field(default=0, ge=0, le=1)
    fee_exponent: float = Field(default=1, ge=1, le=5)
    rules_verified: bool = True
    status: str = "open"
    is_demo: bool = False

    @model_validator(mode="after")
    def valid_market(self):
        if len(self.outcomes) < 2 or len(self.outcomes) != len(set(self.outcomes)):
            raise ValueError("Outcome set must be unique and exhaustive")
        if self.outcome not in self.outcomes:
            raise ValueError("Selection must belong to the outcome set")
        if self.market != "moneyline" and self.line is None:
            raise ValueError("Spread and total markets require an exact line")
        if self.share_price is not None and abs(self.odds - 1 / self.share_price) > 0.001:
            raise ValueError("Contract price and decimal odds disagree")
        for field in ("starts_at", "source_at", "received_at"):
            setattr(self, field, as_utc(getattr(self, field)))
        return self

    @property
    def market_id(self) -> str:
        return stable_id(self.event_id, self.market, self.line, self.ruleset, sorted(self.outcomes))

    @property
    def quote_id(self) -> str:
        return stable_id(self.market_id, self.book, self.outcome)
