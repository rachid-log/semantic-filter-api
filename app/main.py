"""semantic-filter-api — self-hosted text-embedding service.

One job: turn short texts (keywords, titles) into vectors so a workflow (n8n,
a script) can do semantic dedupe without paying a third-party API per call.

Endpoints:
  POST /embed       bearer-protected; texts in, vectors out
  GET  /api/health  open; what the deploy pipeline's artifact guard reads
  GET  /robots.txt  baked at image build time (carries the Host: line the
                    artifact guard compares against SITE_URL)

The model is baked into the image at build time (FASTEMBED_CACHE_PATH points at
a pre-downloaded cache), so the container never downloads at boot.
"""

import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

MODEL_NAME = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
MAX_BATCH = int(os.environ.get("MAX_BATCH", "256"))
SITE_URL = os.environ.get("SITE_URL", "")
GIT_SHA = os.environ.get("GIT_SHA", "")[:12]
API_TOKEN = os.environ.get("EMBED_API_TOKEN", "")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

_state: dict = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Import here so a broken model cache fails at startup (visible in logs and
    # the container healthcheck), not on the first request.
    from fastembed import TextEmbedding

    model = TextEmbedding(model_name=MODEL_NAME)
    # One warm-up call: loads the ONNX session and proves the baked cache works.
    dim = len(next(iter(model.embed(["warm-up"]))))
    _state["model"] = model
    _state["dim"] = dim
    yield
    _state.clear()


app = FastAPI(title="semantic-filter-api", docs_url=None, redoc_url=None, lifespan=lifespan)


class EmbedRequest(BaseModel):
    texts: list[str] = Field(min_length=1, description="Texts to embed, in order")


def require_token(request: Request) -> None:
    # Fail closed: no token configured means no access, not open access. The
    # artifact guard asserts the token is present after every deploy, so this
    # 503 can only be hit by a misconfigured local run.
    if not API_TOKEN:
        raise HTTPException(status_code=503, detail="EMBED_API_TOKEN not configured")
    auth = request.headers.get("authorization", "")
    scheme, _, candidate = auth.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(candidate, API_TOKEN):
        raise HTTPException(status_code=401, detail="invalid bearer token")


@app.get("/")
def root() -> dict:
    return {"service": "semantic-filter-api", "health": "/api/health", "embed": "POST /embed"}


# HEAD explicitly: the container HEALTHCHECK probes with `wget --spider`,
# which sends HEAD, and FastAPI does not answer HEAD for a GET-only route.
@app.api_route("/api/health", methods=["GET", "HEAD"])
def health() -> dict:
    # Flat JSON, string-friendly values: the deploy pipeline parses this with
    # grep/cut, not a JSON parser. Reports that secrets EXIST, never their value.
    return {
        "status": "ok" if _state.get("model") else "starting",
        "siteUrl": SITE_URL,
        "commit": GIT_SHA,
        "model": MODEL_NAME,
        "dim": _state.get("dim", 0),
        "auth": "configured" if API_TOKEN else "missing",
    }


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    # Written at image build time from the SITE_URL build arg — this is the
    # "baked surface" the artifact guard checks against the runtime env.
    return (STATIC_DIR / "robots.txt").read_text()


@app.post("/embed", dependencies=[Depends(require_token)])
def embed(req: EmbedRequest) -> dict:
    if len(req.texts) > MAX_BATCH:
        raise HTTPException(
            status_code=413,
            detail=f"batch of {len(req.texts)} exceeds MAX_BATCH={MAX_BATCH}",
        )
    model = _state.get("model")
    if model is None:
        raise HTTPException(status_code=503, detail="model not loaded yet")
    vectors = [v.tolist() for v in model.embed(req.texts)]
    return {
        "model": MODEL_NAME,
        "dim": _state["dim"],
        "count": len(vectors),
        "embeddings": vectors,
    }
