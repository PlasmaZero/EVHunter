from datetime import timedelta

import pytest

from evhunter.best_bets import ResearchInput, grade_bet, synthetic_research
from evhunter.config import Config
from evhunter.db import Ledger, Preference, default_preferences, initialize, make_engine, transaction
from evhunter.domain import Quote, utcnow
from evhunter.providers import parse_odds_api
from evhunter.services import analyze, save_quotes
from evhunter.sports import SPORT_PROFILES


def event(league):
    now = utcnow()
    return {"id": f"test-{league}", "sport_key": SPORT_PROFILES[league]["api_key"], "home_team": "Home", "away_team": "Away", "commence_time": (now+timedelta(hours=2)).isoformat(),
            "bookmakers": [{"key": "fanduel", "last_update": now.isoformat(), "markets": [{"key": "spreads", "outcomes": [{"name": "Home", "point": -1.5, "price": 2.1}, {"name": "Away", "point": 1.5, "price": 1.85}]}]}]}


@pytest.mark.parametrize("league", list(SPORT_PROFILES))
def test_all_supported_leagues_parse_with_their_settlement_identity(league):
    config = Config(sport_rule_reviews={league: ["fanduel"]}, _env_file=None)
    quotes, rejected = parse_odds_api([event(league)], config)
    assert len(quotes) == 2 and rejected == 0
    assert all(q.sport == league and q.rules_verified and q.ruleset == SPORT_PROFILES[league]["settlement"] for q in quotes)


@pytest.mark.parametrize("league", ["MLB", "NHL", "WNBA", "NCAAB", "NCAAF"])
def test_new_leagues_do_not_inherit_legacy_nba_nfl_rule_approvals(league):
    quotes, _ = parse_odds_api([event(league)], Config(reviewed_books="fanduel", _env_file=None))
    assert quotes and not any(q.rules_verified for q in quotes)


def op(league):
    return {"id": "selection", "market_id": "a"*24, "sport": league, "market": "spread", "line": -1.5, "outcome": "Home", "home": "Home", "away": "Away", "selection": "Home -1.5",
            "edge": 8, "true_probability": .54, "effective_odds": 2, "reference": "draftkings", "method": "proportional", "comparison": [], "source_at": utcnow().isoformat(), "books": ["fanduel"], "size_verified": True, "stake": 100, "is_demo": False}


def review(league):
    r = synthetic_research(op(league), utcnow())
    r.pop("origin")
    r["scoring"]["source_url"] = "https://example.com/stats"
    r["matchup_source_url"] = "https://example.com/matchup"
    r["injury_source_url"] = "https://example.com/availability"
    return r


@pytest.mark.parametrize("league,unit", [("NBA", "points"), ("MLB", "runs"), ("NHL", "goals"), ("WNBA", "points"), ("NCAAB", "points"), ("NCAAF", "points")])
def test_grading_uses_league_units_and_does_not_inject_nfl_matchups(league, unit):
    grade = grade_bet(op(league), default_preferences("live"), review(league))
    assert grade["projection"]["unit"] == unit
    assert grade["sport_profile"]["unit"] == unit
    reasons = " ".join(reason for f in grade["factors"] for reason in f["reasons"])
    if league not in {"NFL", "NCAAF"}:
        assert "WR/CB" not in reasons
    assert 0 <= grade["score"] <= 100 and grade["coverage"] == 100


@pytest.mark.parametrize("league", ["MLB", "NHL"])
def test_unconfirmed_starters_cannot_become_reviewed_candidates(league):
    r = review(league)
    r["availability_checks"] = {}
    grade = grade_bet(op(league), default_preferences("live"), r)
    assert grade["state"] == "provisional" and grade["score"] <= 69
    assert next(f for f in grade["factors"] if f["key"] == "injuries")["points"] == 0
    r["availability_checks"] = {key: True for key in SPORT_PROFILES[league]["required_checks"]}
    assert grade_bet(op(league), default_preferences("live"), r)["coverage"] == 100


@pytest.mark.parametrize("league", ["MLB", "NHL"])
def test_baseball_and_hockey_availability_expires_after_three_hours(league):
    r = review(league)
    r["injury_as_of"] = (utcnow()-timedelta(hours=4)).isoformat()
    assert grade_bet(op(league), default_preferences("live"), r)["state"] == "provisional"


def test_full_baseball_season_samples_are_accepted():
    r = review("MLB")
    r["scoring"]["home_games"] = r["scoring"]["away_games"] = 162
    assert ResearchInput.model_validate(r).scoring.home_games == 162


def test_mlb_quotes_enter_both_ev_and_arb_analysis_only_when_enabled_and_reviewed(tmp_path):
    config = Config(app_env="test", data_mode="live", sports="baseball_mlb", sport_rule_reviews={"MLB": ["fanduel", "draftkings"]}, database_url=f"sqlite:///{tmp_path}/mlb.db", _env_file=None)
    engine = make_engine(config)
    initialize(engine, config)
    now = utcnow()
    common = dict(event_id="mlb-event", sport="MLB", home="Home", away="Away", starts_at=now+timedelta(hours=2), market="moneyline", outcomes=["Home", "Away"], ruleset=SPORT_PROFILES["MLB"]["settlement"], source_at=now, received_at=now)
    with transaction(engine) as session:
        session.get(Ledger, "live:opening").amount = 1000
        save_quotes(session, "live", [Quote(**common, book="fanduel", outcome="Home", selection="Home", odds=2.2), Quote(**common, book="fanduel", outcome="Away", selection="Away", odds=1.8), Quote(**common, book="draftkings", outcome="Home", selection="Home", odds=1.8), Quote(**common, book="draftkings", outcome="Away", selection="Away", odds=2.05)])
        session.flush()
        assert {o["type"] for o in analyze(session, "live")[0]} == {"ev", "arb"}
        pref = session.get(Preference, "live")
        pref.data = {**pref.data, "enabled_sports": []}
        assert analyze(session, "live")[0] == []
        pref.data = {**pref.data, "enabled_sports": ["MLB"], "league_rule_reviews": {"MLB": []}}
        assert analyze(session, "live")[0] == []
    engine.dispose()


def test_dashboard_exposes_all_leagues_and_filters_respect_monitoring(owner):
    data = owner.get("/api/dashboard").json()
    assert set(data["sports"]) == {"NBA", "NFL", "MLB", "NHL", "WNBA", "NCAAB", "NCAAF"}
    assert data["best_bets"]["methodology"]["freshness_by_sport"]["MLB"]["injury_hours"] == 3
    assert data["best_bets"]["methodology"]["freshness_by_sport"]["NFL"]["injury_hours"] == 6
    assert {b["opportunity"]["sport"] for b in data["best_bets"]["items"]} == set(data["sports"])
    prefs = owner.get("/api/settings").json()
    assert owner.put("/api/settings", json={**prefs, "enabled_sports": ["MLB"]}).status_code == 200
    updated = owner.get("/api/dashboard").json()
    assert updated["opportunities"] and all(o["sport"] == "MLB" for o in updated["opportunities"])
    assert updated["collection"]["estimated_monthly_credits"] == 129600
    assert owner.put("/api/settings", json={**prefs, "enabled_sports": ["UNSUPPORTED"]}).status_code == 422


def test_saved_leagues_survive_restart_and_old_clients_preserve_them(owner):
    prefs = owner.get("/api/settings").json()
    assert owner.put("/api/settings", json={**prefs, "enabled_sports": ["MLB", "NBA"]}).status_code == 200
    initialize(owner.app.state.engine, owner.app.state.config)
    assert owner.get("/api/settings").json()["enabled_sports"] == ["MLB", "NBA"]
    prefs.pop("enabled_sports")
    prefs.pop("league_rule_reviews")
    assert owner.put("/api/settings", json=prefs).status_code == 200
    assert owner.get("/api/settings").json()["enabled_sports"] == ["MLB", "NBA"]


def test_baseball_research_rejects_football_availability_keys(owner):
    bet = next(b for b in owner.get("/api/dashboard").json()["best_bets"]["items"] if b["opportunity"]["sport"] == "MLB")
    r = review("MLB")
    r["market_id"], r["outcome"] = bet["opportunity"]["market_id"], bet["opportunity"]["outcome"]
    r["availability_checks"]["quarterback_active"] = True
    assert owner.put("/api/research", json=r).status_code == 422
