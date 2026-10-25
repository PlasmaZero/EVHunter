from evhunter.config import Config
from evhunter.providers import kalshi_asks, parse_odds_api, polymarket_us_asks


def fixture_event():
    return {"id": "event-1", "sport_key": "basketball_nba", "home_team": "Home", "away_team": "Away", "commence_time": "2030-01-01T20:00:00Z", "bookmakers": [{"key": "fanduel", "last_update": "2030-01-01T19:00:00Z", "markets": [{"key": "spreads", "outcomes": [{"name": "Home", "point": -3.5, "price": 1.95}, {"name": "Away", "point": 3.5, "price": 1.95}]}]}]}


def test_home_spread_normalization():
    quotes, rejected = parse_odds_api([fixture_event()], Config(reviewed_books="fanduel", _env_file=None))
    assert not rejected and len(quotes) == 2
    assert quotes[0].market_id == quotes[1].market_id
    assert quotes[0].line == quotes[1].line == -3.5
    assert quotes[0].rules_verified


def test_disagreeing_spreads_are_excluded():
    event = fixture_event()
    event["bookmakers"][0]["markets"][0]["outcomes"][1]["point"] = 4.5
    assert parse_odds_api([event], Config(_env_file=None))[0] == []


def test_missing_source_timestamp_is_not_replaced_with_fetch_time():
    event = fixture_event()
    del event["bookmakers"][0]["last_update"]
    assert parse_odds_api([event], Config(_env_file=None))[0] == []


def test_kalshi_complementary_bids_are_executable_asks():
    yes, no = kalshi_asks({"orderbook_fp": {"yes_dollars": [["0.42", "100"]], "no_dollars": [["0.55", "75"]]}})
    assert yes == [(.45, 75)] and no == [(.58, 100)]


def test_polymarket_us_uses_offers_and_opposite_bids():
    yes, no = polymarket_us_asks({"marketData": {"state": "MARKET_STATE_OPEN", "offers": [{"px": {"value": "0.54", "currency": "USD"}, "qty": "8"}], "bids": [{"px": {"value": "0.51", "currency": "USD"}, "qty": "12"}]}})
    assert yes == [(.54, 8)] and no == [(.49, 12)]


def test_suspended_us_market_is_excluded():
    assert polymarket_us_asks({"marketData": {"state": "MARKET_STATE_SUSPENDED"}}) == ([], [])
