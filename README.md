# Site Chatbot

A small, self-hosted “chat with my data” demonstration for this VPS. It combines:

- a small Python web application;
- heading-aware hybrid BM25 and vector retrieval over approved Markdown, text, and JSON files;
- FastEmbed embeddings stored persistently in Qdrant;
- a CPU-only, quantized LiquidAI LFM3 1.7B Q4_K_M model served by llama.cpp;
- HTTPS routing through the VPS's existing Traefik instance.

The browser never talks to the model directly. The model container has no
published host port, and the app supplies only the passages retrieved from
`data/`.

## Add your content

Update `data/tom-facts.md` or add `.md`, `.txt`, and `.json` files. Keep secrets,
private customer data, credentials, and instructions that should not be public
out of this directory.

The BM25 index and Qdrant collection are rebuilt when the app container starts.
Markdown is split by heading into chunks of no more than 140 lexical tokens so
the title and heading fit comfortably within MiniLM's 256-wordpiece embedding
limit. Every chunk is embedded with `sentence-transformers/all-MiniLM-L6-v2`
and stored in the persistent `qdrant-data` Docker volume:

```bash
docker compose restart app
```

To refresh the public content from `tomvsaji.com`:

```bash
python3 sync_site.py
docker compose restart app
```

## Configure

```bash
cp .env.example .env
```

Set `CHAT_HOST` to a hostname whose DNS points at this VPS. The included Hostinger
hostname already resolves to this machine.

The public defaults are deliberately conservative:

```dotenv
REQUESTS_PER_MINUTE=3
REQUESTS_PER_HOUR=20
MAX_MODEL_REQUESTS=1
MAX_QUESTION_CHARS=800
ALLOWED_ORIGINS=https://chat.srv1619516.hstgr.cloud,https://tomvsaji.com,https://www.tomvsaji.com
```

`ALLOWED_ORIGINS` controls which browser origins may call `/api/chat` for the
native website widget. It is not an authentication mechanism. Keep the app port
private behind Traefik.

## Test

```bash
python3 -m unittest discover -s tests -v
python3 app.py
```

The second command can test the interface, but retrieval needs Qdrant and chat
responses need the model service. To run the grounded-answer regression suite
against the Compose deployment:

```bash
docker compose exec app python evaluate.py
```

The evaluation contains source-grounded multi-turn scenarios covering real
article titles, explanatory depth, repeated follow-ups, category boundaries,
unsupported questions, prompt injection, topic changes, and latency. See
`tests/EVAL_SET.md` for the cases, scoring rules, core-suite filter, and
model-comparison commands.

The local-model and RAG comparison is documented in
`MODEL_EVALUATION_REPORT.md`, with machine-readable results under
`eval-results/`.

The browser uses `POST /api/chat/stream`, which returns Server-Sent Events:
`status` while retrieval/generation runs, individually validated `claim` events,
then a `final` answer with sources. The original JSON `POST /api/chat` endpoint
remains available for non-streaming clients.

## Deploy

```bash
docker compose up -d --build
docker compose logs -f app llm qdrant
```

Run `bash ops/download-model` before the first start. Once the model
reports that the server is listening, visit the configured HTTPS hostname.

Useful checks:

```bash
docker compose ps
docker compose logs --tail=100 app llm
curl https://chat.srv1619516.hstgr.cloud/health
```

## Native widget abuse controls

See [SECURITY.md](SECURITY.md) for the request contract, proxy trust boundaries,
bounded quotas and HTTP resources, optional Turnstile setup, production release
checklist, and rollback. New settings are wired in `compose.yaml` and
`.env.example`. Proxy trust defaults to empty and challenges default to off;
configure verified Traefik peers before relying on per-visitor quotas.
In-memory quotas apply to one process and reset on restart.
The standalone `/` UI (`static/app.js`) has no Turnstile flow, so enabling
`TURNSTILE_REQUIRED` blocks its chat submissions. It has no challenge exemption;
the main native frontend supplies tokens independently. See the
[Turnstile compatibility details](SECURITY.md#optional-turnstile).

## Production notes

- This 2-vCPU server is suitable for a demo and light personal traffic, not many
  simultaneous users.
- Keep the minute/hour limits and single-request global model capacity enabled.
  Add an upstream challenge if the endpoint attracts sustained distributed abuse.
- Back up `data/` and pin container images to tested immutable digests.
- Retrieval uses reciprocal-rank fusion to combine semantic Qdrant results with
  exact-term BM25 matches, followed by a relevance gate. The model must return
  schema-constrained cited claims; malformed or unsupported output is discarded.
- Ad revenue is unlikely to justify abuse and inference costs at small scale.
  Start with a useful portfolio feature, measure demand, then decide whether to
  monetize it.

## Development workflow

GitHub is the source of truth at `tomvsaji/site-chatbot`. The `main` branch is
production and is deployed automatically only after its GitHub Actions checks
pass. Day-to-day VPS work belongs on `develop` or a short-lived branch and
reaches `main` through a pull request.

Run the fast test suite before pushing:

```bash
python3 -m unittest discover -s tests -v
docker compose -f compose.dev.yaml -p site-chatbot-dev config -q
```

The VPS can run a localhost-only preview without duplicating the memory-heavy
LLM container. It starts an isolated development app and Qdrant instance, while
connecting to the production LLM over its private Docker network:

```bash
docker compose -f compose.dev.yaml -p site-chatbot-dev up -d --build
docker compose -f compose.dev.yaml -p site-chatbot-dev logs -f app-dev
curl http://127.0.0.1:8081/health
docker compose -f compose.dev.yaml -p site-chatbot-dev down
```

From another computer, preview it through an SSH tunnel:

```bash
ssh -L 8081:127.0.0.1:8081 YOUR_VPS_USER@YOUR_VPS_HOST
```

Then open `http://127.0.0.1:8081`. Stop the preview when finished because its
requests share the production model's limited inference capacity. Development
Qdrant data remains separate in the `site-chatbot-dev_qdrant-dev-data` volume.

After a pull request is merged, GitHub Actions sends the exact merge commit SHA
to the VPS's restricted deployment command. Production is rebuilt from the
clean checkout at `/srv/site-chatbot`, checked through the public `/health`
endpoint, and rolled back to the prior commit automatically if validation
fails. The VPS `.env`, model cache, embedding cache, and Qdrant data are never
stored in GitHub.

This repository currently has no open-source license. Copyright remains with
the repository owner, and no reuse rights are granted except where required by
law.

## LFM3 operation (2026-11-09)

Production is `/srv/site-chatbot`; editable source is `/home/codex/site-chatbot`.
The post-trained `LiquidAI/LFM3-1.7B` uses the official
`LiquidAI/LFM3-1.7B-GGUF` Q4_K_M artifact at revision
`cd0c74c575b5c416f3011b5b4c298077f26ca8c2`. File SHA256:
`3793e1e6e2266919d6e015287221b41100175a98eddaba92e111aed3b05c93c4`.
The file is 1,099,627,808 bytes (check installed file for authoritative size).
It is downloaded separately; weights are excluded from Git and image builds.

The CPU runtime is llama.cpp `0.8.0-dev`, build `11960`, commit `8642541b2`,
image `ghcr.io/ggml-org/llama.cpp:server-b11960`, pinned by digest in Compose.
Liquid's official documentation requires b11960 or later for LFM3:
https://docs.liquid.ai/deployment/on-device/llama-cpp . Model reference:
https://huggingface.co/LiquidAI/LFM3-1.7B . Do not use the older generic
installation examples on that page as an LFM3 version recommendation.

From the chosen checkout:

```bash
bash ops/download-model
docker compose -p site-chatbot config -q
docker compose -p site-chatbot up -d --build
curl --fail http://127.0.0.1:8082/health
curl --fail https://chat.srv1619516.hstgr.cloud/health
docker compose -p site-chatbot logs --tail 50 app llm
```

Keep `.env` server-side with mode 0600. `CHAT_HOST`, `SITE_NAME`,
`ALLOWED_ORIGINS`, `REQUESTS_PER_MINUTE`, `REQUESTS_PER_HOUR`,
`MAX_MODEL_REQUESTS`, `MAX_QUESTION_CHARS`, `MIN_SEMANTIC_SCORE`,
`MIN_LEXICAL_SCORE`, `EMBEDDING_MODEL`, and `QDRANT_COLLECTION` configure
hosting and retrieval. Compose fixes the chat model to `LiquidAI/LFM3-1.7B`;
`LLM_BASE_URL` is internal `http://llm:8080/v1`. No chat provider key is needed.
There is no alternate chat model route or browser model selector. Do not expose
llm or Qdrant ports publicly. App port 8082 is loopback-only for diagnostics;
Traefik provides existing public TLS routing. Existing origin restrictions and
rate limits remain. This site has no login in its existing application.

Two CPU threads, one inference slot, 4096 context tokens and 320 output tokens (512 for explanatory requests) (512 for explanatory requests)
bound inference work. Sampling is temperature 0.1, top-k 50 and repeat penalty
1.1. Reasoning is disabled. The browser uses SSE; only complete validated cited
claims stream to the user. The last eight messages, each at most 800 characters,
resolve follow-up retrieval; prior assistant prose is excluded from generation.
The preserved MiniLM embedding model and Qdrant/BM25 retrieval supply factual
site context. Unsupported questions still abstain. Inference failures return
HTTP 503 for JSON chat or an SSE error event. App `/health` reports retrieval
readiness and configuration, not model readiness; test a real chat after every
deployment. One extra attempt against the same model may check strong sources.

Runtime limits are 2500 MiB for inference, 768 MiB for app, 512 MiB for Qdrant.
Total runtime use exceeds weight size. Measure cgroup `memory.peak` inside each
container and `docker stats`; page-cache accounting differs between these.
Python dependency versions measured on this deployment: fastembed 0.7.4,
qdrant-client 1.16.2, ONNX Runtime 1.31.1. The existing requirements ranges are
retained; rebuilds can resolve newer compatible dependencies.

The deployment hook downloads/checks the pinned weights before starting.
Copy reviewed source into the deployment checkout or deploy a committed main
revision through the existing restricted SSH hook. This session deployed local
files; publishing changes upstream is a separate step. Do not run the old
installer against this running deployment: it checks out upstream source.
