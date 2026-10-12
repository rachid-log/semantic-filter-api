# syntax=docker/dockerfile:1
#
# Python FastAPI service wrapping a CPU embedding model (fastembed / ONNX),
# listening on :8080, with Traefik terminating TLS in front.
#
# The model is downloaded ONCE, at build time, into a cache layer — the
# container must never download at boot. Changing EMBED_MODEL is therefore a
# rebuild, not a restart.

FROM python:3.12-slim AS deps
WORKDIR /app
COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ── bake the model ───────────────────────────────────────────────────────────
FROM python:3.12-slim AS modelcache
ENV PATH="/opt/venv/bin:$PATH" \
    FASTEMBED_CACHE_PATH=/opt/fastembed-cache
COPY --from=deps /opt/venv /opt/venv

# Build-time values only. Secrets are readable forever via `docker history`, so
# they stay runtime-only through env_file.
ARG EMBED_MODEL=BAAI/bge-small-en-v1.5
ENV EMBED_MODEL=$EMBED_MODEL

# Download AND exercise the model: a corrupt or missing cache fails the build,
# not the first request in production.
RUN mkdir -p "$FASTEMBED_CACHE_PATH" && python - <<'PY'
import os
from fastembed import TextEmbedding
model = TextEmbedding(model_name=os.environ["EMBED_MODEL"])
vec = list(model.embed(["build-time smoke test"]))[0]
assert len(vec) > 0, "empty embedding at build time"
print(f"baked {os.environ['EMBED_MODEL']} dim={len(vec)}")
PY

# ── runner ───────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runner
WORKDIR /app

# wget: the compose HEALTHCHECK and the deploy pipeline's artifact guard both
# probe the container with it, and slim ships without it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends wget \
    && rm -rf /var/lib/apt/lists/*

RUN addgroup --system --gid 1001 appgroup \
    && adduser --system --uid 1001 --ingroup appgroup app

ENV PATH="/opt/venv/bin:$PATH" \
    FASTEMBED_CACHE_PATH=/opt/fastembed-cache \
    PYTHONUNBUFFERED=1 \
    # Keep ONNX from grabbing every core on a server shared with other apps.
    OMP_NUM_THREADS=2

COPY --from=deps /opt/venv /opt/venv
COPY --from=modelcache /opt/fastembed-cache /opt/fastembed-cache
COPY app ./app

ARG EMBED_MODEL=BAAI/bge-small-en-v1.5
ENV EMBED_MODEL=$EMBED_MODEL
ARG GIT_SHA
ENV GIT_SHA=$GIT_SHA

# The baked surface the artifact guard reads: robots.txt carries the Host: line
# compiled in at build time, proving which SITE_URL this image was built for.
ARG SITE_URL
ENV SITE_URL=$SITE_URL
RUN mkdir -p static \
    && printf 'User-agent: *\nDisallow: /\n\nHost: %s\n' "$SITE_URL" > static/robots.txt \
    # Fail the build on a placeholder domain — cheaper than catching it after
    # the swap, and it catches what CI's env override cannot. The committed
    # .env.example uses embed.example.com on purpose: you must set your own.
    && grep -q '^Host: https\?://.' static/robots.txt \
    && ! grep -qi 'example\.com\|__REQUIRED__' static/robots.txt

# The model cache too — it was written as root in the modelcache stage, and a
# root-owned cache makes hf_hub's metadata reads fail with EACCES at runtime.
RUN chown -R app:appgroup /app /opt/fastembed-cache
USER app

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD wget -q --spider http://127.0.0.1:8080/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
