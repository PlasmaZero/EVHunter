"""Real-service checks run in CI; isolated schemas never touch application tables."""
import os
import secrets
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url

from evhunter.api import create_app
from evhunter.cache import Cache
from evhunter.config import Config
from evhunter.db import Ledger, initialize, make_engine, transaction


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL") or not os.getenv("TEST_REDIS_URL"), reason="Requires PostgreSQL and Redis (provided by CI)")
def test_postgres_startup_lock_concurrent_bankroll_and_redis():
    url = os.environ["TEST_DATABASE_URL"]
    admin = create_engine(url)
    schema = "evhunter_test_"+secrets.token_hex(6)
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    test_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    config = Config(app_env="test", data_mode="live", embed_worker=False, database_url=test_url, redis_url=os.environ["TEST_REDIS_URL"], _env_file=None)
    engine = make_engine(config)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: initialize(engine, config), range(2)))
        with transaction(engine) as session:
            assert len(session.scalars(select(Ledger)).all()) == 1
        cache = Cache(config)
        assert cache.redis.ping()
        address = "integration-"+secrets.token_hex(6)
        assert all(cache.login_allowed(address) for _ in range(10))
        assert not cache.login_allowed(address)
        cache.redis.delete(f"evhunter:login:{address}")
        with TestClient(create_app(config)) as client:
            response = client.post("/api/auth/login", json={"username": "daniel", "password": "evhunter-demo-2026"})
            client.headers["X-CSRF-Token"] = response.json()["csrf"]
            assert client.post("/api/bankroll", json={"kind": "deposit", "amount": 100}).status_code == 201
            payload = {"event": "A @ B", "selection": "A", "book": "fanduel", "stake": 80, "odds": 1.91}
            with ThreadPoolExecutor(max_workers=2) as pool:
                codes = list(pool.map(lambda _: client.post("/api/bets", json=payload).status_code, range(2)))
            assert sorted(codes) == [201, 422]
            assert client.get("/api/bets").json()["summary"]["available"] == 20
            bet = client.get("/api/bets").json()["bets"][0]
            assert client.post(f"/api/bets/{bet['id']}/settle", json={"status": "won"}).json()["profit"] == 72.8
            assert client.get("/api/bets").json()["summary"]["bankroll"] == 172.8
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
