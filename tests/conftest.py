import os
import tempfile

import pytest

_database = tempfile.TemporaryDirectory(prefix="fieldscope-tests-")
os.environ["DATABASE_URL"] = "sqlite:///" + _database.name.replace("\\", "/") + "/test.db"
os.environ["QVAC_REQUIRED"] = "false"
os.environ["QVAC_ENABLED"] = "false"

from database import engine, SessionLocal
from models import Base
from main import app
from fastapi.testclient import TestClient


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()
    _database.cleanup()


@pytest.fixture
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def save(client):
    def capture(text, reporter="Demo Reporter"):
        response = client.post("/api/observations/process", json={"text": text, "submitted_by": reporter})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body.get("session_id"), body
        result = client.post("/api/observations/confirm", json={"session_id": body["session_id"]})
        assert result.status_code == 200, result.text
        return result.json()
    return capture
