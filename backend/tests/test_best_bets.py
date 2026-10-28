from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from evhunter.api import create_app
from evhunter.best_bets import ResearchInput, grade_bet, scoring_projection, synthetic_research
from evhunter.db import default_preferences
from evhunter.domain import utcnow


def opportunity(**changes):
    now = utcnow().isoformat()
    return {"id": "bet", "market_id": "a"*24, "outcome": "Home", "type": "ev", "home": "Home", "away": "Away", "event": "Away @ Home", "sport": "NFL", "market": "spread", "line": -2.5,
            "selection": "Home -2.5", "edge": 8.0, "effective_odds": 2.0, "true_probability": .54,
            "reference": "draftkings", "method": "proportional", "comparison": [], "source_at": now,
            "books": ["fanduel"], "size_verified": True, "stake": 100, "is_demo": False, **changes}


def research(op=None):
    op = op or opportunity()
    data = synthetic_research(op, utcnow())
    data["origin"] = "owner"
    data["scoring"]["source_url"] = "https://example.com/stats"
    data["matchup_source_url"] = "https://example.com/matchup"
    data["injury_source_url"] = "https://example.com/injuries"
    return data


def test_missing_research_never_means_clear_injuries_or_a_reviewed_candidate():
    op = opportunity()
    before = deepcopy(op)
    grade = grade_bet(op, default_preferences("live"))
    assert 0 <= grade["score"] <= 69
    assert grade["coverage"] == 65 and grade["state"] == "provisional"
    assert grade["factors"][2]["points"] == 0
    assert op == before  # Research scores never mutate EV, probability, or Kelly sizing.


def test_complete_review_is_bounded_and_preserves_rubric_sum():
    result = grade_bet(opportunity(), default_preferences("live"), research())
    assert result["state"] == "candidate" and result["coverage"] == 100
    assert result["score"] == round(sum(f["points"] for f in result["factors"]))
    assert 70 <= result["score"] <= 100
    assert sum(f["max_points"] for f in result["factors"]) == 100


@pytest.mark.parametrize("status,cap", [("material", 59), ("unresolved", 49)])
def test_injury_risk_blocks_candidates_even_when_ev_is_large(status, cap):
    r = research()
    r["injury_status"] = status
    grade = grade_bet(opportunity(edge=50), default_preferences("live"), r)
    assert grade["score"] <= cap and grade["state"] != "candidate"


def test_stale_injury_report_loses_points_and_becomes_provisional():
    r = research()
    r["injury_as_of"] = (utcnow()-timedelta(hours=7)).isoformat()
    grade = grade_bet(opportunity(), default_preferences("live"), r)
    assert grade["factors"][2]["points"] == 0
    assert grade["state"] == "provisional" and grade["score"] <= 69
    r["injury_status"] = "unresolved"
    assert grade_bet(opportunity(), default_preferences("live"), r)["score"] <= 49


def test_research_quality_determines_candidates_independently_of_budget():
    r = research()
    r["scoring"]["home_games"] = 2
    assert grade_bet(opportunity(), default_preferences("live"), r)["state"] == "provisional"
    op = opportunity(stake=0)
    graded = grade_bet(op, default_preferences("live"), research())
    assert graded["state"] == "candidate"
    assert graded["score"] == grade_bet(opportunity(), default_preferences("live"), research())["score"]
    assert op["stake"] == 0  # Grading does not create a stake suggestion without a budget.


@pytest.mark.parametrize("market,line,outcome,cushion", [
    ("spread", -2.5, "Home", 2), ("spread", -2.5, "Away", -2),
    ("total", 45.5, "over", 1), ("total", 45.5, "under", -1),
    ("moneyline", None, "Home", 4.5), ("moneyline", None, "Away", -4.5),
])
def test_scoring_baseline_uses_the_exact_handicap_and_outcome(market, line, outcome, cushion):
    op = opportunity(market=market, line=line, outcome=outcome)
    projection = scoring_projection(op, research()["scoring"])
    assert projection["home"] == 25.5 and projection["away"] == 21
    assert projection["selection_cushion"] == cushion


def test_quote_age_reduces_grade_and_retail_reference_is_not_treated_as_sharp():
    pref, op, r = default_preferences("live"), opportunity(), research()
    fresh = grade_bet(op, pref, r)
    old = grade_bet({**op, "source_at": (utcnow()-timedelta(seconds=110)).isoformat()}, pref, r)
    sharp = grade_bet({**op, "reference": "pinnacle"}, pref, r)
    assert old["score"] < fresh["score"] < sharp["score"]


def payload(op):
    r = research(op)
    r.pop("origin")
    return r


def test_research_requires_auth_and_csrf(client):
    assert client.put("/api/research", json=payload(opportunity())).status_code == 401
    client.post("/api/auth/login", json={"username": "daniel", "password": "evhunter-demo-2026"})
    assert client.put("/api/research", json=payload(opportunity())).status_code == 403


def test_sourced_research_persists_and_regrades_without_changing_ev(owner):
    initial = owner.get("/api/dashboard").json()
    item = initial["best_bets"]["items"][0]
    op = item["opportunity"]
    r = payload(op)
    r["injury_status"] = "unresolved"
    r["injury_notes"] = "Starting receiver questionable; workload decision pending."
    saved = owner.put("/api/research", json=r)
    assert saved.status_code == 200 and saved.json()["origin"] == "owner"
    updated = owner.get("/api/dashboard").json()
    rows = [g for g in updated["best_bets"]["items"] if g["research_key"] == item["research_key"]]
    assert rows and all(g["score"] <= 49 and not g["synthetic_research"] for g in rows)
    assert rows[0]["opportunity"]["true_probability"] == op["true_probability"]
    assert owner.delete(f"/api/research/{item['research_key']}").status_code == 200


def test_other_market_research_cannot_be_saved_and_invalid_sources_are_rejected(owner):
    assert owner.put("/api/research", json=payload(opportunity())).status_code == 422
    op = owner.get("/api/dashboard").json()["best_bets"]["items"][0]["opportunity"]
    r = payload(op)
    r["injury_source_url"] = "javascript:alert(1)"
    assert owner.put("/api/research", json=r).status_code == 422
    r = payload(op)
    r["matchup_notes"] = ""
    assert owner.put("/api/research", json=r).status_code == 422


def test_live_mode_never_inherits_synthetic_demo_research(config, owner):
    op = owner.get("/api/dashboard").json()["best_bets"]["items"][0]["opportunity"]
    assert owner.put("/api/research", json=payload(op)).status_code == 200
    with TestClient(create_app(config.model_copy(update={"data_mode": "live"}))) as live:
        login = live.post("/api/auth/login", json={"username": "daniel", "password": "evhunter-demo-2026"})
        live.headers["X-CSRF-Token"] = login.json()["csrf"]
        result = live.get("/api/dashboard").json()
        assert result["best_bets"]["items"] == []
        assert live.put("/api/research", json=payload(op)).status_code == 422


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_nonfinite_statistics_cannot_enter_grading(value):
    r = payload(opportunity())
    r["scoring"]["home_for"] = value
    with pytest.raises(ValidationError):
        ResearchInput.model_validate(r)


def test_future_dated_and_timezone_free_reviews_are_rejected():
    r = payload(opportunity())
    r["injury_as_of"] = utcnow()+timedelta(days=1)
    with pytest.raises(ValidationError):
        ResearchInput.model_validate(r)
    r["injury_as_of"] = "2026-10-27T12:00:00"
    with pytest.raises(ValidationError):
        ResearchInput.model_validate(r)
