from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import app
from bb import config, fixtures

client = TestClient(app)


@pytest.fixture()
def offline(monkeypatch):
    monkeypatch.setattr(config, "TAVILY_KEY", "")
    monkeypatch.setattr(config, "GROQ_KEY", "")
    monkeypatch.setattr(config, "BIFROST_URL", "")


def test_index_and_health_render():
    index = client.get("/")
    assert index.status_code == 200
    assert "Best Before" in index.text

    health = client.get("/api/health").json()
    assert health["ok"] is True
    assert "thresholds" in health
    assert health["thresholds"]["current_at"] == 0.72


def test_samples_endpoint_returns_full_answers():
    payload = client.get("/api/samples").json()
    assert len(payload["samples"]) >= 3
    first = payload["samples"][0]
    assert first["answer"]
    assert first["claims"] > 0


def test_check_endpoint_scores_a_sample(offline):
    sample = fixtures.get("stack-2023")
    response = client.post("/api/check", json={"text": sample["answer"], "sample_id": sample["id"]})
    assert response.status_code == 200
    report = response.json()["report"]
    assert report["verdict"]["status"] == "expired"
    assert report["verdict"]["counts"]["stale"] == 6
    assert report["mode"]["evidence"] == "recorded"
    assert len(report["claims"]) == 8


def test_check_endpoint_labels_unverified_text(offline):
    response = client.post("/api/check", json={"text": "Python 3.12 is the latest stable release."})
    assert response.status_code == 200
    assert response.json()["report"]["verdict"]["status"] == "unverified"


def test_short_text_is_rejected():
    assert client.post("/api/check", json={"text": "hi"}).status_code == 422
