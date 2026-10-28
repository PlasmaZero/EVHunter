import asyncio
import base64

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from fastapi.testclient import TestClient

from evhunter import worker
from evhunter.api import create_app
from evhunter.cache import Cache
from evhunter.config import Config
from evhunter.db import Ledger, Preference, SourceState, initialize, make_engine, transaction
from evhunter.providers import ProviderError, request_json
from evhunter.streams import NovigBook, credential_status, signing_headers


def pem(key):
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def test_old_bookmaker_environment_drops_paid_only_fanatics():
    config = Config(bookmakers=" fanduel, fanatics, draftkings,fanduel ", _env_file=None)
    assert config.bookmakers == "fanduel,draftkings"
    with pytest.raises(ValueError):
        Config(bookmakers="fanatics", _env_file=None)


def test_auto_streams_use_inline_keys_and_accept_polymarket_us_sdk_names(monkeypatch):
    key = ed25519.Ed25519PrivateKey.generate()
    monkeypatch.setenv("POLYMARKET_KEY_ID", "test-sdk-id")
    monkeypatch.setenv("POLYMARKET_SECRET_KEY", base64.b64encode(key.private_bytes_raw()).decode())
    config = Config(prediction_streams="", kalshi_api_key_id="kalshi-test", kalshi_private_key=pem(key).replace("\n", "\\n"),
                    novig_key_id="novig-test", novig_private_key=pem(key), _env_file=None)
    assert set(config.stream_sources) == {"kalshi", "novig", "polymarket", "polymarket_us"}
    assert all(credential_status(source, config)["status"] == "configured" for source in ["kalshi", "novig", "polymarket_us"])
    headers = signing_headers("kalshi", config, 1800000000000)
    key.public_key().verify(base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"]), b"1800000000000GET/trade-api/ws/v2")
    assert config.polymarket_us_key_id == "test-sdk-id"
    assert Config(prediction_streams="off", _env_file=None).stream_sources == []
    assert Config(prediction_streams="kalshi, novig", _env_file=None).stream_sources == ["kalshi", "novig"]
    with pytest.raises(ValueError):
        Config(prediction_streams="bad-venue", _env_file=None)


def test_free_feed_migration_preserves_exchange_selection_and_bankroll(owner):
    engine, config = owner.app.state.engine, owner.app.state.config
    with transaction(engine) as session:
        session.get(Ledger, "demo:opening").amount = 12345
        pref = session.get(Preference, "demo")
        pref.data = {**pref.data, "allowed_books": ["fanduel", "fanatics", "draftkings", "kalshi"],
                     "reference_book": "fanatics", "min_ev": 7.5, "league_rule_reviews": {"NBA": ["fanatics", "fanduel", "kalshi"]}}
        session.delete(session.get(SourceState, "demo:free-sportsbooks-v1"))
    initialize(engine, config)
    with transaction(engine) as session:
        pref = session.get(Preference, "demo").data
        assert pref["allowed_books"] == ["fanduel", "draftkings", "kalshi"]
        assert pref["reference_book"] == "draftkings" and pref["min_ev"] == 7.5
        assert pref["league_rule_reviews"]["NBA"] == ["fanduel", "kalshi"]
        assert session.get(Ledger, "demo:opening").amount == 12345


def test_sources_report_credential_problems_without_returning_secrets(tmp_path):
    secret = "test-only-bad-private-material"
    config = Config(app_env="test", data_mode="live", embed_worker=False, odds_api_key="test-only-odds-secret",
                    kalshi_api_key_id="test-only-key-id", kalshi_private_key=secret,
                    database_url=f"sqlite:///{tmp_path}/setup.db", _env_file=None)
    with TestClient(create_app(config)) as client:
        assert client.get("/api/sources").status_code == 401
        login = client.post("/api/auth/login", json={"username": config.owner_username, "password": config.owner_password}).json()
        response = client.get("/api/sources")
        data = response.json()
        assert data["setup"]["odds_api"]["status"] == "configured"
        assert data["setup"]["kalshi"]["status"] == "invalid"
        assert data["setup"]["polymarket"]["status"] == "public"
        assert data["configured_books"] == ["fanduel", "draftkings"]
        assert secret not in response.text and config.odds_api_key not in response.text and config.kalshi_api_key_id not in response.text
        assert secret not in repr(config)
        assert client.post("/api/sources/retry").status_code == 403
        client.headers["X-CSRF-Token"] = login["csrf"]
        with transaction(client.app.state.engine) as session:
            session.add(SourceState(id="live:odds_api", data={"retry_at": "2030-01-01T00:00:00Z", "last_attempt": "2029-01-01T00:00:00Z", "budget_spent": 123}))
        assert client.post("/api/sources/retry").json()["scheduled"] is True
        with transaction(client.app.state.engine) as session:
            state = session.get(SourceState, "live:odds_api").data
            assert state["retry_at"] is None and state["last_attempt"] is None and state["budget_spent"] == 123


@pytest.mark.parametrize("http_status,body,state", [(401, {"error_code": "INVALID_API_KEY"}, "authentication_failed"),
                                                   (401, {"error_code": "OUT_OF_USAGE_CREDITS"}, "quota_exhausted"),
                                                   (429, {}, "rate_limited")])
def test_provider_errors_are_actionable_and_never_include_api_key(http_status, body, state):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(http_status, json={**body, "message": str(request.url)}, headers={"retry-after": "90"}))) as client:
            with pytest.raises(ProviderError) as caught:
                await request_json(client, "https://provider.example/odds?apiKey=secret-must-not-leak")
            assert caught.value.status == state
            assert caught.value.retry_after > 0
            assert "secret-must-not-leak" not in str(caught.value)
    asyncio.run(run())


def test_novig_documented_lifecycle_transitions_clear_books_atomically():
    decoder = NovigBook([{"external_id": "market", "token_ids": ["yes", "no"]}])
    snapshot = {"ts": 1800000000000, "snapshot": {"market": {"lifecycle": {"seq": 1, "status": "OPEN"},
                "book": {"seq": 1, "orders": {"yes": [{"order": "order", "price": ".4", "qty": 50}]}}}}}
    assert decoder.process(snapshot)[0].books[1] == [(.6, 50)]
    closed = {"ts": 1800000000001, "delta": {"market": {"lifecycle": {"seq": 2, "deltas": ["CLOSE"]},
              "book": {"seq": 2, "deltas": [{"kind": "remove", "order": "order"}]}}}}
    assert decoder.process(closed)[0].books == ([], [])
    assert decoder.process({"ts": 1800000000002, "delta": {"market": {"lifecycle": {"seq": 3, "deltas": ["OPEN"]}}}})[0].books == ([], [])
    added = {"ts": 1800000000003, "delta": {"market": {"book": {"seq": 3, "deltas": [{"kind": "add", "order": "new", "outcome": "no", "price": ".5", "qty": 20}]}}}}
    assert decoder.process(added)[0].books[0] == [(.5, 20)]


@pytest.mark.parametrize("budget,code,expected,requests", [(2, 200, "budget_paused", 0), (450, 401, "quota_exhausted", 1), (450, 200, "no_data", 1)])
def test_worker_respects_free_budget_and_quota_cooldowns(tmp_path, monkeypatch, budget, code, expected, requests):
    config = Config(app_env="test", data_mode="live", embed_worker=False, sports="basketball_nba", odds_api_key="test-odds-key",
                    monthly_credit_budget=budget, database_url=f"sqlite:///{tmp_path}/worker.db", _env_file=None)
    engine = make_engine(config)
    initialize(engine, config)
    calls = []
    real_client = httpx.AsyncClient
    def handler(request):
        calls.append(request)
        assert request.url.params["bookmakers"] == "fanduel,draftkings"
        return httpx.Response(code, json=[] if code == 200 else {"error_code": "OUT_OF_USAGE_CREDITS"}, headers={"x-requests-last": "3"})
    monkeypatch.setattr(worker.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    async def run():
        await worker.tick(engine, config, Cache(config))
        await worker.tick(engine, config, Cache(config))
    asyncio.run(run())
    assert len(calls) == requests
    with transaction(engine) as session:
        state = session.get(SourceState, "live:odds_api").data
        assert state["status"] == expected
        assert state["budget_spent"] <= budget
        if requests == 0:
            assert not state.get("last_success")
    engine.dispose()
