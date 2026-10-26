"""Read-only provider adapters. No sportsbook scraping or order-entry endpoints."""
import json
from datetime import datetime, timezone
from urllib.parse import quote as urlquote

import httpx

from .config import Config
from .domain import Quote, as_utc, utcnow

SPORTS = {"basketball_nba": "NBA", "americanfootball_nfl": "NFL"}
MARKETS = {"h2h": "moneyline", "spreads": "spread", "totals": "total"}


class ProviderError(Exception):
    pass


async def request_json(client, url, **kwargs):
    try:
        response = await client.get(url, **kwargs)
        response.raise_for_status()
        return response.json(), response.headers
    except httpx.HTTPStatusError as exc:
        # Exception strings include request URLs and can expose API keys. Never log them.
        raise ProviderError(f"Provider returned HTTP {exc.response.status_code}") from None
    except (httpx.RequestError, ValueError):
        raise ProviderError("Provider is unreachable or returned invalid JSON") from None


def parse_odds_api(events: list, config: Config, received_at=None) -> tuple[list[Quote], int]:
    now = received_at or utcnow()
    quotes, rejected = [], 0
    fees = json.loads(config.exchange_fee_config)
    reviewed = set(config.reviewed_books.split(","))
    for event in events:
        sport = SPORTS.get(event.get("sport_key"))
        if not sport:
            continue
        for book in event.get("bookmakers", []):
            for market in book.get("markets", []):
                kind = MARKETS.get(market.get("key"))
                if not kind:
                    continue
                rows = market.get("outcomes", [])
                if len(rows) != 2:
                    rejected += len(rows)
                    continue
                outcomes = ["over", "under"] if kind == "total" else [event["home_team"], event["away_team"]]
                try:
                    timestamp = market.get("last_update") or book.get("last_update")
                    if not timestamp:
                        raise ValueError("No source timestamp")
                    line = None
                    if kind == "spread":
                        home = next(o for o in rows if o["name"] == event["home_team"])
                        away = next(o for o in rows if o["name"] == event["away_team"])
                        line = float(home["point"])
                        if abs(line + float(away["point"])) > 1e-8:
                            raise ValueError("Non-complementary spreads")
                    elif kind == "total":
                        if float(rows[0]["point"]) != float(rows[1]["point"]):
                            raise ValueError("Non-complementary totals")
                        line = float(rows[0]["point"])
                    fee = fees.get(book["key"], {})
                    is_exchange = book["key"] in ["novig", "prophetx"]
                    for row in rows:
                        outcome = row["name"].lower() if kind == "total" else row["name"]
                        selection = f"{row['name']} {float(row['point']):+g}" if kind == "spread" else f"{row['name']} {row['point']:g}" if kind == "total" else row["name"]
                        quotes.append(Quote(event_id=event["id"], sport=sport, home=event["home_team"], away=event["away_team"],
                                            starts_at=as_utc(event["commence_time"]), market=kind, line=line,
                                            ruleset=f"{sport.lower()}-full-game-ot-no-push-v1", outcome=outcome, outcomes=outcomes,
                                            selection=selection, book=book["key"], odds=float(row["price"]), source_at=as_utc(timestamp), received_at=now,
                                            max_stake=float(row["bet_limit"]) if row.get("bet_limit") else None,
                                            rules_verified=book["key"] in reviewed or book["key"] == "pinnacle",
                                            fee_model=(fee.get("model", "unknown") if fee.get("reviewed_at") else "unknown") if is_exchange else "none",
                                            fee_rate=fee.get("rate", 0)))
                except (KeyError, ValueError, StopIteration, TypeError):
                    rejected += len(rows)
    return quotes, rejected


async def collect_sportsbook(client, config, sport_key):
    data, headers = await request_json(client, f"{config.odds_api_base_url}/sports/{sport_key}/odds", params={
        "apiKey": config.odds_api_key, "bookmakers": config.bookmakers,
        "markets": "h2h,spreads,totals", "oddsFormat": "decimal", "includeBetLimits": "true"})
    if not isinstance(data, list):
        raise ProviderError("Unexpected odds response schema")
    quotes, rejected = parse_odds_api(data, config)
    return quotes, rejected, {"credits_remaining": headers.get("x-requests-remaining"), "credits_used": headers.get("x-requests-used"), "last_cost": int(headers.get("x-requests-last", 3))}


def kalshi_asks(book: dict) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    if "orderbook_fp" in book:
        raw = book["orderbook_fp"]
        yes = [(float(p), float(n)) for p, n in raw.get("yes_dollars", [])]
        no = [(float(p), float(n)) for p, n in raw.get("no_dollars", [])]
    else:
        raw = book.get("orderbook", {})
        yes = [(float(p)/100, float(n)) for p, n in raw.get("yes", [])]
        no = [(float(p)/100, float(n)) for p, n in raw.get("no", [])]
    return sorted([(round(1-p, 6), n) for p, n in no]), sorted([(round(1-p, 6), n) for p, n in yes])


def polymarket_us_asks(book: dict):
    raw = book.get("marketData", {})
    if raw.get("state") != "MARKET_STATE_OPEN":
        return [], []
    offers = sorted([(float(r["px"]["value"]), float(r["qty"])) for r in raw.get("offers", [])])
    no = sorted([(round(1-float(r["px"]["value"]), 6), float(r["qty"])) for r in raw.get("bids", [])])
    return offers, no


async def collect_prediction(client, config, mapping: dict, exemplar: Quote):
    source = mapping["source"]
    identifier = urlquote(mapping["external_id"], safe="")
    now = utcnow()
    stamp, basis = now, "snapshot"
    if source == "kalshi":
        data, _ = await request_json(client, f"{config.kalshi_base_url}/markets/{identifier}/orderbook", params={"depth": 1})
        metadata, _ = await request_json(client, f"{config.kalshi_base_url}/markets/{identifier}")
        if metadata.get("market", {}).get("status") not in ["active", "open"]:
            return []
        books = kalshi_asks(data)
    elif source == "polymarket_us":
        data, _ = await request_json(client, f"{config.polymarket_us_base_url}/v1/markets/{identifier}/book")
        books = polymarket_us_asks(data)
        ts = data.get("marketData", {}).get("transactTime")
        if ts:
            stamp, basis = as_utc(ts), "source"
    elif source == "polymarket":
        books = []
        timestamps = []
        for token in mapping["token_ids"]:
            data, _ = await request_json(client, f"{config.polymarket_base_url}/book", params={"token_id": token})
            if data.get("market") and data["market"] != mapping["external_id"]:
                raise ProviderError("Token belongs to a different condition")
            books.append(sorted([(float(r["price"]), float(r["size"])) for r in data.get("asks", [])]))
            if data.get("timestamp"):
                timestamps.append(datetime.fromtimestamp(int(data["timestamp"])/1000, timezone.utc))
        if timestamps:
            stamp, basis = min(timestamps), "source"
    else:
        raise ProviderError("Unsupported prediction venue")
    return make_prediction_quotes(mapping, exemplar, books, stamp, basis)


def make_prediction_quotes(mapping, exemplar, books, stamp, basis="source"):
    source = mapping["source"]
    now = utcnow()
    payout = .01 if source == "novig" else 1.0
    yes = mapping["yes_outcome"]
    no = next(o for o in exemplar.outcomes if o != yes)
    quotes = []
    for outcome, ladder in zip([yes, no], books):
        if not ladder:
            continue
        price, size = ladder[0]
        if not 0 < price < 1 or size < 1:
            continue
        selection = (f"{'Over' if outcome == 'over' else 'Under'} {exemplar.line}" if exemplar.market == "total" else
                     f"{outcome} {exemplar.line if outcome == exemplar.home else -exemplar.line:+g}" if exemplar.market == "spread" else outcome)
        values = {**exemplar.model_dump(), "book": source, "outcome": outcome, "selection": selection,
            "odds": 1/price, "share_price": price*payout, "contract_payout": payout, "contracts": size, "max_stake": None,
            "fee_model": mapping["fee_model"], "fee_rate": mapping["fee_rate"], "fee_exponent": mapping.get("fee_exponent", 1),
            "source_at": stamp, "timestamp_basis": basis, "received_at": now, "rules_verified": True,
            "is_demo": False, "feed": "direct", "status": "open"}
        quotes.append(Quote.model_validate(values))
    return quotes
