# How we built the Semantic Filter API

This is the build journey, step by step. The goal: stop paying twice for the same keyword.
A keyword workflow in **n8n** collects keywords from many sources, then sends each one to a
**paid** research run (a scraper, a SERP or keyword-data API). Many keywords are the same
idea in other words: "seo tools", "best seo tools", "top seo tools". We wanted to catch
those **before** the paid run, cheaply and privately.

It covers what we chose, what broke, and why. Built by Rachid Ait Mohand, with Claude Code
as a pair programmer. It started on 2026-08-14 as a private project and became public on
2026-10-13.

```mermaid
flowchart LR
  A["2026-08-14<br/>Why self-host"] --> B["Model + runtime<br/>bge-small, ONNX"]
  B --> C["Bake the model<br/>into the image"]
  C --> D["The API<br/>fail-closed auth"]
  D --> E["Tests on the<br/>real model"]
  E --> F["Deploy layer<br/>+ artifact guard"]
  F --> G["Onboarding a<br/>new machine"]
  G --> H["2026-09-23<br/>API.md"]
  H --> I["2026-10-13<br/>Public release"]
```

---

## Step 1. Why self-host (2026-08-14)

- **The job is small and repeats a lot.** Turn a short text into a vector. Do it for every
  keyword, in every workflow run.
- **Text matching is not enough.** "seo tools" and "best seo tools" are different strings.
  Only a meaning-based comparison sees that they are the same search.
- **An embeddings API from a vendor works, but:**
  - it bills **per call**, and an automation makes many small calls;
  - it has **rate limits**;
  - the keyword lists **leave your server**;
  - the vendor can **retire the model**, and then old vectors no longer match new ones.
- **The server was already there.** A small VPS (about 2 GB of RAM) ran n8n and other apps
  behind Traefik. A small embedding service could live next to them for no extra cost.
- **Decision:** a self-hosted service with one job. Text in, vectors out. No storage. The
  caller (n8n) compares the vectors.

---

## Step 2. Pick the model and the runtime

- **Model: `BAAI/bge-small-en-v1.5`.**
  - 384 numbers per vector. Small, fast, and enough for short phrases.
  - English, which matched the keyword lists.
  - **MIT license**, so commercial use is fine.
  - Strong on short phrases, which is exactly what keywords are.
- **Runtime: fastembed on ONNX Runtime, not PyTorch.**
  - The server has about 2 GB of RAM, shared with other apps.
  - PyTorch is a big install and a big memory user. ONNX Runtime runs the same model on CPU
    with far less.
  - fastembed downloads a ready-made ONNX file of the model and handles the tokenizer.
  - We wrote it down in `requirements.txt`: "no torch, which is what keeps the image and the
    RSS small enough for the 2GB box".
- **Measured later (2026-10-13):** about 245 MB of RAM idle, about 350 MB after 256-text
  batches, image about 800 MB.

---

## Step 3. Bake the model into the image

The first rule: **the container must never download the model at boot.**

- A download at boot makes every restart depend on Hugging Face being up.
- It makes the first start slow.
- It could even fetch a **different** file than the one you tested.

So the Dockerfile has a middle stage that downloads the model **and uses it once**:

```python
model = TextEmbedding(model_name=os.environ["EMBED_MODEL"])
vec = list(model.embed(["build-time smoke test"]))[0]
assert len(vec) > 0, "empty embedding at build time"
```

- **A broken or missing model fails the build**, not the first request in production.
- `FASTEMBED_CACHE_PATH=/opt/fastembed-cache` points the app at the baked copy.
- **A trap, noted in the Dockerfile:** the cache is written as root during the build, but
  the app runs as a normal user (uid 1001). A root-owned cache makes Hugging Face's library
  fail to read its own metadata with `EACCES` (permission denied) at runtime. → The
  Dockerfile runs `chown` on the cache folder.
- **Consequence:** changing `EMBED_MODEL` is a **rebuild**, not a restart.

---

## Step 4. Design the API

One route does the work: `POST /embed`. Everything else supports it.

- **Batches, with a cap.** `{"texts": [...]}` in, vectors out, in the same order.
  `MAX_BATCH=256` by default. More → **413**. The reason: protect a 2 GB server from an
  accidental 50,000-row batch out of n8n.
- **Empty list → 422.** Pydantic checks it (`min_length=1`).
- **Fail-closed auth.** A Bearer token, compared in constant time
  (`secrets.compare_digest`). **No token configured means no access (503), never open
  access.**
- **Load at startup, not on the first request.** The model loads in FastAPI's `lifespan` and
  embeds one warm-up text. A broken model shows up in the logs and the healthcheck at once.
- **Health that a shell script can read.** `/api/health` returns **flat** JSON with string
  values. The deploy reads it with `grep` and `cut`, not a JSON parser. It says if the token
  **exists** (`"configured"`), never its value.
- **`HEAD` on the health route.** Docker's healthcheck uses `wget --spider`, which sends
  `HEAD`. FastAPI does not answer `HEAD` on a `GET`-only route (405), so the container would
  never become healthy. → `@app.api_route("/api/health", methods=["GET", "HEAD"])`.
- **Interactive docs off.** `/docs` and `/redoc` are disabled. The service is private plumbing.
- **Share the CPU fairly.** `OMP_NUM_THREADS=2` in the image, one uvicorn worker. Speed comes
  from batching, not from more workers.

---

## Step 5. Test against the real model

- **No mocks.** The tests load the real model. A broken fastembed version fails the tests,
  not production.
- **The test that matters most** checks the reason the service exists: "seo tools" vs
  "best seo tools" must score above 0.8, and at least 0.2 above "chocolate cake recipe".
- **CI** (GitHub Actions) runs ruff and pytest, and caches the model download between runs.
- **CI also builds the real image**, with `SITE_URL=https://ci.invalid`. A broken Dockerfile
  then fails in review, not halfway through a deploy on the server.

---

## Step 6. The deploy layer and the artifact guard

The deploy layer came from a reusable template for "one app, one compose stack, behind the
server's Traefik". It was first written for Node apps and adapted to Python here. It keeps
the same contract: the same health fields, the same split between config files.

- **Push to `main` deploys.** GitHub Actions connects over SSH and runs the deploy on the server.
- **Build while the old container serves.** A build error leaves the live service untouched.
- **Rollback is one tag.** Before each build, the running image is tagged `:previous`. The
  build happens on the server so that tag is always there. The cleanup step only removes
  dangling layers (`docker image prune -f`), because `docker system prune -a` would delete the
  rollback.
- **`.env` is rendered from GitHub secrets.** Every key in `.env.example` is replaced by a
  secret of the same name, if one exists. Required keys ship as `__REQUIRED__`, and the deploy
  fails if one is left.
  - **A trap the template had already fixed:** a search for `__REQUIRED__` anywhere in the
    file also matches the **comments that explain** the sentinel, so every first deploy
    fails. → The check only matches an assignment: `^KEY=.*__REQUIRED__`.
- **Two env files, on purpose.** `.env` goes into the container. `.env.deploy` (server address,
  SSH key path, GitHub token) stays on the developer's machine. A bug in the app must not
  hand over the server.
- **Secrets never become build args.** Build args stay readable forever with `docker history`.
- **The artifact guard.** A container can be "healthy" with 200s and a valid certificate while
  it runs an **old build**. So after the swap, the deploy compares what is actually served
  with what it just configured:
  - the `Host:` line in `robots.txt`, written **at build time** from `SITE_URL`;
  - `siteUrl`, `commit`, `auth` and `model` from `/api/health`, read **at runtime**.
  Any mismatch fails the job and prints the rollback command.
- **The build refuses a placeholder domain.** If `robots.txt` would say `example.com` or
  `__REQUIRED__`, the image build stops. That is cheaper than finding it after the swap.
- **Log rotation** (3 × 10 MB). On a shared disk, an unrotated log becomes an outage for
  every app, with a confusing cause.

---

## Step 7. Onboarding a new machine (2026-08-14, later that evening)

The second commit answered: "what does a new laptop need?"

- **To deploy: nothing.** GitHub Actions holds every secret. A fresh clone deploys with
  `git push`.
- **For manual ops** (`scripts/deploy.sh`, `scripts/vps.sh`): the server details. In the
  private version they came from a private credential store, with a local `.env.deploy` file
  as an override. In the public version: `.env.deploy`, or variables exported from **your
  own secret store**.
- **SSH keys: one per machine, never copied.** A stolen laptop is then revoked by deleting
  one line in the server's `authorized_keys`. The other machines keep working. Copying a
  private key doubles the places it can leak from.

---

## Step 8. API.md (2026-09-23)

- A full written spec of every endpoint: health fields, request and response bodies, and every
  error code.
- **The math, written down:** the vectors have length 1.0, so the **dot product is the cosine
  similarity**. No square roots needed in the n8n Code node.
- **First threshold guidance:** about 0.85–0.90 and up = near-duplicate, under 0.60 = a
  different topic.
- On 2026-09-25 a one-line comment change was pushed just to trigger a deploy. The
  workflow's **Run workflow** button (`workflow_dispatch`) does the same without a commit.

---

## Step 9. The clean public release (2026-10-13)

### Why a fresh history

- The old history held **live infrastructure details**: the server's IP and hostname, the
  production domain, the private credential-store address, and personal machine paths.
- Deleting them in a new commit is not enough. Anyone can read old commits.
- **So the public repo starts from one new commit.** The private repo stays as it is and keeps
  deploying production.

### What changed

- **New name: `semantic-filter-api`.** It says what the service is for: filtering by meaning.
  The FastAPI title, the service, container, image and Traefik router names all changed.
- **The environment variable names did not change** (`EMBED_API_TOKEN`, `EMBED_MODEL`,
  `MAX_BATCH`, `SITE_URL`, `GIT_SHA`), so existing setups keep working.
- **Placeholders instead of real infrastructure:** `203.0.113.10` (an IP reserved for
  documentation), `embed.example.com`, and a `deploy` user. The build's placeholder guard now
  forces every new user to set a real domain.
- **Deploy works for anyone:**
  - the deploy job only runs when the repo variable `DEPLOY_ENABLED` is `true`, so the
    public repo and fresh forks do not fail on every push;
  - it clones `${{ github.repository }}`, so forks need no edit;
  - `GH_PAT` is only needed for a private copy;
  - CI now also runs on `main`.
- **Leftovers removed.** The template's database and migration steps, and the `psql` and
  `backup` commands, did nothing here: this service has no database.
- **One README.** `API.md` and `DEPLOYMENT.md` were folded into it.

### Bugs and edge cases found while preparing it

- **`ModuleNotFoundError: No module named 'app'`.** Plain `pytest` does not put the repo root
  on `sys.path`. → `pythonpath = ["."]` in `pyproject.toml`.
- **The manual deploy checked less than the pipeline.** It skipped the baked domain, the token
  and the model. → It now runs the same five checks.
- **`HEAD /` answers 405.** The old runbook said `curl -sI https://<host>` should show 200. It
  never could. → Checks now use `/api/health`, which answers `HEAD`.
- **`/robots.txt` answers 500 outside Docker.** The file is created at image build.
- **An empty string is embedded, not refused.** Callers should filter blanks.
- **`"texts": "seo tools"`** (a string, not a list) → 422 `Input should be a valid list`.
- **The model size in the old docs was wrong.** They said about 130 MB. That is the original
  model. fastembed downloads a **66.5 MB** ONNX file.

### Real numbers, measured on 2026-10-13

| "seo tools" vs | Score |
|---|---|
| "best seo tools" | **0.954** |
| "top seo tools" | 0.944 |
| "seo software" | 0.863 |
| "keyword research tool" | **0.777** |
| "backlink checker" | 0.696 |
| "how to bake bread" | **0.449** |

- In the Docker image, on a laptop CPU with 2 threads: 1 text in about 28 ms, 256 texts in
  about 720 ms.
- A new container answers after about 5 s.
- About 245 MB of RAM idle.

### Publishing

- **License:** Apache-2.0 for the code. The model (MIT) is downloaded at build time, not stored
  in the repo (see `NOTICE`).
- **What stays out:** `.env`, `.env.deploy`, the live URL, and anything about the real server.
- **`.intropera.json`** and a 16:9 cover put the project on intropera.com. The images in
  `media/` are for LinkedIn and X. `media/build_media.py` renders them with headless Chrome.

---

## Lessons

- **Measure the thing you depend on.** The docs said 130 MB. The real download is 66.5 MB.
  The old runbook expected a 200 that the code could never return.
- **Bake models into the image.** A boot that downloads is a boot that can fail.
- **Fail closed.** A missing token must lock the door, not open it.
- **Check the running artifact, not the config.** Two config files can agree with each other
  while the container runs something else.
- **Make placeholders fail loudly.** `__REQUIRED__` and the `example.com` build guard turn a
  forgotten setting into a red build, not a quiet production bug.
- **Anchor your sentinel checks.** A search for `__REQUIRED__` that also matches its own
  explanation fails on documentation.
- **Keep secrets out of build args** and out of the file the app can read.
- **Test with the real model.** The similarity test protects the reason the service exists.
- **Publish from a fresh history** when the old one holds infrastructure details.
