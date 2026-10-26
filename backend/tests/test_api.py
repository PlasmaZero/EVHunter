from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from evhunter.api import create_app
from evhunter.db import CurrentQuote, Ledger, transaction
from evhunter.domain import utcnow


def bet_payload(**changes):
    return {"event": "A @ B", "selection": "Over 220.5", "book": "fanduel", "market": "total", "stake": 100, "odds": 1.91, **changes}


def test_authentication_and_csrf(client):
    assert client.get("/api/dashboard").status_code == 401
    assert client.post("/api/auth/login", json={"username": "daniel", "password": "wrong"}).status_code == 401
    login = client.post("/api/auth/login", json={"username": "daniel", "password": "evhunter-demo-2026"})
    assert login.status_code == 200
    assert client.post("/api/bets", json=bet_payload()).status_code == 403
    client.headers["X-CSRF-Token"] = login.json()["csrf"]
    assert client.post("/api/bets", json=bet_payload(), headers={"Origin": "https://attacker.example"}).status_code == 403


def test_login_is_rate_limited(client):
    responses = [client.post("/api/auth/login", json={"username": "x", "password": "x"}).status_code for _ in range(11)]
    assert responses[-1] == 429


def test_dashboard_computes_signals_from_quotes(owner):
    data = owner.get("/api/dashboard").json()
    assert data["mode"] == "demo"
    assert data["counts"]["quote_count"] == 96
    assert {o["type"] for o in data["opportunities"]} == {"ev", "arb"}
    for op in data["opportunities"]:
        assert op["is_demo"]
        if op["type"] == "arb":
            assert min(l["payout"] for l in op["legs"])-op["stake"] >= op["profit"]-1e-8


def test_manual_bet_without_probability_and_idempotent_settlement(owner):
    before = owner.get("/api/bets").json()["summary"]
    logged = owner.post("/api/bets", json=bet_payload())
    assert logged.status_code == 201
    bid = logged.json()["id"]
    current = owner.get("/api/bets").json()["summary"]
    assert current["available"] == before["available"]-100
    assert current["bankroll"] == before["bankroll"]
    won = owner.post(f"/api/bets/{bid}/settle", json={"status": "won"})
    assert won.status_code == 200 and won.json()["profit"] == 91
    assert owner.post(f"/api/bets/{bid}/settle", json={"status": "won"}).status_code == 200
    assert owner.post(f"/api/bets/{bid}/settle", json={"status": "lost"}).status_code == 409
    after = owner.get("/api/bets").json()["summary"]
    assert after["bankroll"] == before["bankroll"]+91
    assert after["exposure"] == 0


def test_actual_fees_and_accepted_odds_determine_profit(owner):
    b = owner.post("/api/bets", json=bet_payload(stake=125, entry_fee=1, win_fee=2, probability=.55)).json()
    assert b["stake"] == 126
    result = owner.post(f"/api/bets/{b['id']}/settle", json={"status": "won"}).json()
    assert result["profit"] == 110.75
    assert result["predicted_ev"] == (.55*(125*1.91-2)/126-1)*100


def test_void_entry_fee_policy(owner):
    b = owner.post("/api/bets", json=bet_payload(entry_fee=2)).json()
    assert owner.post(f"/api/bets/{b['id']}/settle", json={"status": "void", "refund_entry_fee": False}).json()["profit"] == -2


def test_cannot_overspend_or_withdraw_reserved_cash(owner):
    available = owner.get("/api/bets").json()["summary"]["available"]
    assert owner.post("/api/bets", json=bet_payload(stake=available+1)).status_code == 422
    assert owner.post("/api/bets", json=bet_payload(stake=available)).status_code == 201
    assert owner.post("/api/bankroll", json={"kind": "withdrawal", "amount": 1}).status_code == 422


def test_deposits_are_not_profit(owner):
    before = owner.get("/api/bets").json()["summary"]
    owner.post("/api/bankroll", json={"kind": "deposit", "amount": 100})
    after = owner.get("/api/bets").json()["summary"]
    assert after["bankroll"] == before["bankroll"]+100
    assert after["profit"] == before["profit"]


def test_stale_quotes_expire_instead_of_becoming_fresh(owner):
    with transaction(owner.app.state.engine) as session:
        for q in session.scalars(select(CurrentQuote)).all():
            q.data = {**q.data, "source_at": (utcnow()-timedelta(hours=1)).isoformat()}
    data = owner.get("/api/dashboard").json()
    assert data["opportunities"] == []
    assert data["counts"]["rejected"]["stale"] == 96


def test_linked_snapshot_survives_strategy_changes(owner):
    signal = next(o for o in owner.get("/api/dashboard").json()["opportunities"] if o["type"] == "ev")
    b = owner.post("/api/bets", json=bet_payload(snapshot_id=signal["snapshot_id"], odds=2.2, event=signal["event"], selection=signal["selection"])).json()
    prefs = owner.get("/api/settings").json()
    owner.put("/api/settings", json={**prefs, "devig_method": "power"})
    stored = next(r for r in owner.get("/api/bets").json()["bets"] if r["id"] == b["id"])
    assert stored["snapshot"]["method"] == "proportional"
    assert stored["true_probability"] == signal["true_probability"]
    assert stored["predicted_ev"] == (signal["true_probability"]*2.2-1)*100


def test_live_records_have_no_demo_fallback(config):
    with TestClient(create_app(config.model_copy(update={"data_mode": "live"}))) as live:
        live.post("/api/auth/login", json={"username": "daniel", "password": "evhunter-demo-2026"})
        result = live.get("/api/dashboard").json()
        assert result["opportunities"] == []
        assert result["counts"]["quote_count"] == 0
        assert result["bankroll"]["bankroll"] == 0


def test_standalone_calculators_and_invalid_inputs(owner):
    fair = owner.post("/api/calculate", json={"kind": "devig", "odds": [-110, -110]}).json()
    assert fair["probabilities"] == [.5, .5]
    arb = owner.post("/api/calculate", json={"kind": "arb", "odds": [110, 105]}).json()
    assert arb["arb"] and arb["profit"] > 37
    assert owner.post("/api/calculate", json={"kind": "devig", "odds": [99, -110]}).status_code == 422


def test_csv_export_escapes_formula_cells(owner):
    owner.post("/api/bets", json=bet_payload(event="=HYPERLINK(example)"))
    response = owner.get("/api/bets/export")
    assert response.status_code == 200
    assert "'=HYPERLINK" in response.text


def test_invalid_mapping_cannot_enter_comparisons(owner):
    response = owner.post("/api/mappings", json={"market_id": "a"*24, "source": "kalshi", "external_id": "KXTEST", "yes_outcome": "A", "fee_model": "none", "fee_rate": 0, "rules_verified": False, "fees_reviewed": True})
    assert response.status_code == 422
