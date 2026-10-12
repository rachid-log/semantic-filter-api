"""API tests against the real model — auth, shape, batching, similarity.

The model downloads on first run (cached via FASTEMBED_CACHE_PATH in CI), so
these are integration tests, not mocks: a broken fastembed pin fails here.
"""

import math
import os

os.environ.setdefault("EMBED_API_TOKEN", "test-token")
os.environ.setdefault("MAX_BATCH", "8")

import pytest
from fastapi.testclient import TestClient

from app.main import app

TOKEN = os.environ["EMBED_API_TOKEN"]
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="module")
def client():
    # Context manager runs the lifespan, which loads the model.
    with TestClient(app) as c:
        yield c


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return dot / (na * nb)


def test_health_is_open_and_flat(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["auth"] == "configured"
    assert body["dim"] > 0
    # The artifact guard parses this with grep — nested JSON would break it.
    assert all(not isinstance(v, (dict, list)) for v in body.values())


def test_health_answers_head(client):
    # The Docker HEALTHCHECK probes with `wget --spider`, which sends HEAD.
    # A GET-only route would answer 405 and the container would never be healthy.
    assert client.head("/api/health").status_code == 200


def test_root_names_the_service(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "semantic-filter-api"


def test_embed_requires_token(client):
    assert client.post("/embed", json={"texts": ["x"]}).status_code == 401
    bad = {"Authorization": "Bearer wrong"}
    assert client.post("/embed", json={"texts": ["x"]}, headers=bad).status_code == 401


def test_embed_shape(client):
    r = client.post("/embed", json={"texts": ["seo tools", "plumbing"]}, headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert len(body["embeddings"]) == 2
    assert all(len(v) == body["dim"] for v in body["embeddings"])


def test_empty_batch_rejected(client):
    assert client.post("/embed", json={"texts": []}, headers=AUTH).status_code == 422


def test_oversize_batch_rejected(client):
    texts = ["x"] * 9  # MAX_BATCH forced to 8 above
    assert client.post("/embed", json={"texts": texts}, headers=AUTH).status_code == 413


def test_similarity_orders_correctly(client):
    """The reason this service exists: near-duplicate keywords must score far
    above unrelated ones."""
    r = client.post(
        "/embed",
        json={"texts": ["seo tools", "best seo tools", "chocolate cake recipe"]},
        headers=AUTH,
    )
    a, b, c = r.json()["embeddings"]
    near = cosine(a, b)
    far = cosine(a, c)
    assert near > 0.8, f"near-duplicates scored {near:.3f}"
    assert near > far + 0.2, f"no separation: near={near:.3f} far={far:.3f}"
