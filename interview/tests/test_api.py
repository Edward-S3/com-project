"""Simpler API tests."""

from fastapi.testclient import TestClient

from app.main import app
from app.rubrics_util import strip_hidden_facts


def test_health_and_meta():
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    meta = client.get("/api/meta").json()
    assert len(meta["difficulties"]) == 3


def test_create_user_and_strip_hidden():
    client = TestClient(app)
    res = client.post(
        "/api/users",
        json={
            "name": "APIテスト",
            "department": "製造",
            "age": 30,
            "tenure_years": 5,
            "grade": "3",
            "consent": True,
        },
    )
    assert res.status_code == 200
    persona = {"hidden_facts": ["secret"], "name": "x"}
    safe = strip_hidden_facts(persona)
    assert "hidden_facts" not in safe
