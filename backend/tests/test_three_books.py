from datetime import timedelta

from sqlalchemy import select

from evhunter.config import Config
from evhunter.db import Ledger, Preference, SourceState, initialize, make_engine, transaction
from evhunter.domain import DEFAULT_EXECUTION_BOOKS, Quote, utcnow
from evhunter.services import analyze, save_quotes


def test_defaults_and_demo_use_only_selected_books(owner):
    assert Config(_env_file=None).bookmakers.split(",") == DEFAULT_EXECUTION_BOOKS
    dashboard = owner.get("/api/dashboard").json()
    assert dashboard["preferences"]["reference_book"] == "draftkings"
    assert dashboard["preferences"]["allowed_books"] == DEFAULT_EXECUTION_BOOKS
    assert all(set(o["books"]) <= set(DEFAULT_EXECUTION_BOOKS) for o in dashboard["opportunities"])
    sources = owner.get("/api/sources").json()
    assert {b for b, count in sources["quote_counts"].items() if count} == set(DEFAULT_EXECUTION_BOOKS)
    assert all(o["books"] != ["draftkings"] for o in dashboard["opportunities"] if o["type"] == "ev")


def test_reference_book_remains_eligible_for_arbitrage(tmp_path):
    config = Config(app_env="test", data_mode="live", embed_worker=False, database_url=f"sqlite:///{tmp_path}/arb.db", _env_file=None)
    engine = make_engine(config)
    initialize(engine, config)
    now = utcnow()
    with transaction(engine) as session:
        session.get(Ledger, "live:opening").amount = 1000
        common = dict(event_id="example", sport="NBA", home="A", away="B", starts_at=now+timedelta(hours=3), market="moneyline", outcomes=["A", "B"], ruleset="nba-full-game-ot-no-push-v1", source_at=now, received_at=now)
        save_quotes(session, "live", [Quote(**common, outcome="A", selection="A", book="fanduel", odds=2.1), Quote(**common, outcome="B", selection="B", book="draftkings", odds=2.05)])
        session.flush()
        opportunities, _ = analyze(session, "live")
        arb = next(o for o in opportunities if o["type"] == "arb")
        assert set(arb["books"]) == {"fanduel", "draftkings"}
        assert arb["profit"] > 0
    engine.dispose()


def test_existing_preferences_migrate_once_without_changing_cash(owner):
    engine, config = owner.app.state.engine, owner.app.state.config
    before = owner.get("/api/bets").json()["summary"]
    with transaction(engine) as session:
        pref = session.get(Preference, "demo")
        pref.data = {**pref.data, "allowed_books": ["fanduel", "betmgm", "fanatics", "draftkings"], "reference_book": "pinnacle"}
        session.delete(session.get(SourceState, "demo:three-book-defaults-v1"))
    initialize(engine, config)
    migrated = owner.get("/api/settings").json()
    assert migrated["allowed_books"] == DEFAULT_EXECUTION_BOOKS
    assert migrated["reference_book"] == "draftkings"
    assert owner.get("/api/bets").json()["summary"] == before
    with transaction(engine) as session:
        pref = session.get(Preference, "demo")
        pref.data = {**pref.data, "reference_book": "fanatics"}
    initialize(engine, config)
    assert owner.get("/api/settings").json()["reference_book"] == "fanatics"
