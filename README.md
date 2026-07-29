# Site Chatbot

A small, self-hosted “chat with my data” demonstration for this VPS. It combines:

- a small Python web application;
- heading-aware hybrid BM25 and vector retrieval over approved Markdown, text, and JSON files;
- FastEmbed embeddings stored persistently in Qdrant;
- a CPU-only, quantized IBM Granite 4.0 1B model served by llama.cpp;
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
MAX_MODEL_REQUESTS=2
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

The browser uses `POST /api/chat/stream`, which returns Server-Sent Events:
`status` while retrieval/generation runs, individually validated `claim` events,
then a `final` answer with sources. The original JSON `POST /api/chat` endpoint
remains available for non-streaming clients.

## Deploy

```bash
docker compose up -d --build
docker compose logs -f app llm qdrant
```

The first start downloads the model and can take several minutes. Once the model
reports that the server is listening, visit the configured HTTPS hostname.

Useful checks:

```bash
docker compose ps
docker compose logs --tail=100 app llm
curl https://chat.srv1619516.hstgr.cloud/health
```

## Production notes

- This 2-vCPU server is suitable for a demo and light personal traffic, not many
  simultaneous users.
- Keep the minute/hour limits and two-request global model capacity enabled.
  Add an upstream challenge if the endpoint attracts sustained distributed abuse.
- Back up `data/` and pin container images to tested immutable digests.
- Retrieval uses reciprocal-rank fusion to combine semantic Qdrant results with
  exact-term BM25 matches, followed by a relevance gate. Granite must return
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
