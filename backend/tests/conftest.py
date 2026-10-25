import pytest
from fastapi.testclient import TestClient

from evhunter.api import create_app
from evhunter.config import Config


@pytest.fixture
def config(tmp_path):
    return Config(app_env="test", data_mode="demo", database_url=f"sqlite:///{tmp_path}/test.db", redis_url="", embed_worker=False, _env_file=None)


@pytest.fixture
def client(config):
    with TestClient(create_app(config)) as client:
        yield client


@pytest.fixture
def owner(client):
    response = client.post("/api/auth/login", json={"username": "daniel", "password": "evhunter-demo-2026"})
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    return client
