# Current optimized deployment

Runtime: post-trained LiquidAI/LFM3-1.7B, official Q4_K_M GGUF revision
`cd0c74c575b5c416f3011b5b4c298077f26ca8c2`, SHA256
`3793e1e6e2266919d6e015287221b41100175a98eddaba92e111aed3b05c93c4`.
llama.cpp server **b11960**, image digest pinned in compose.yaml. CPU threads: 2;
context: 8192; planning: 192 tokens/query at most 240 characters; answers: 640
tokens with a 1200-character model answer field before selected-source formatting.
The separate optional RAM prompt-cache budget is 256 MiB, not total runtime RAM.

The host's fast profile in `/srv/site-chatbot/.env` is:

```dotenv
MAX_AGENT_SEARCHES=1
MEMORY_TOKEN_BUDGET=3600
LLM_PROMPT_CACHE_MIB=256
RECENT_HISTORY_MESSAGES=4
MAX_SESSION_TURNS=40
MEMORY_TTL_DAYS=7
```

The source default allows two model-selected searches; setting the search budget
to 2 permits one refinement at extra latency. One bounded citation/format repair
and token-budget compression can add generation calls. No query keyword router,
new chat model, answer cache or new embedding model is introduced.

## Validate and deploy

Backend checks use Python 3.12 and requirements.txt:

```sh
python -m unittest discover -s tests -v
node --test tests/static-stream.test.mjs
CHAT_HOST=ci.invalid docker compose -f compose.yaml config -q
docker compose -f compose.dev.yaml -p site-chatbot-dev config -q
```

The main branch requires a pull request and its `test` check. Merge the reviewed
exact head through that policy. Deployment uses the existing service account and
locked, health-checked helper with automatic source rollback:

```sh
sudo -u sitechat-deploy /usr/local/sbin/deploy-site-chatbot deploy EXACT_MAIN_SHA
```

The helper fetches main, verifies the SHA, checks the pinned weights, and executes
`docker compose -p site-chatbot up -d --build --remove-orphans` from
`/srv/site-chatbot`. A cache-budget change recreates the model service too.
Preserve the existing private `.env` and persistent conversation, Qdrant and
embedding-cache volumes. Back up `.env` privately before edits; never publish it.
Do not use `down -v` or delete model weights for this optimization.

Development Compose uses the production model service on its existing external
network, so it inherits that model's cache budget. It has no separate model service.
Keep all verification secrets on the API server. The public frontend uses the
same-origin Netlify edge route; it needs no browser API credential.

## Preview and verify

Portfolio PR #16 / `feat/native-chatbot` deploys to
https://deploy-preview-16--tomvsaji.netlify.app/?ntl-drawer-state=hidden .
The query parameter hides Netlify's review toolbar, which can cover the launcher.
Frontend checks: Node 22.21.1, `npm ci --legacy-peer-deps`, `npm test`, `npm run build`.

Check `/health`, ask a factual question, ask for a conversation summary and first
question, and test Clear. The secure HttpOnly session cookie must persist across
turns and rotate on successful Clear. Factual answers show the model-selected
source references. Conversation replies can be source-free. Geek stats now shows
Prompt cache, Cached prompt tokens and Prompt reuse. These describe runtime prompt/
KV reuse across all generation calls, including planning/compression, not a cached
answer. Explicit zero and missing telemetry are different states.

Ordinary structured final answers remain buffered until validation; progress SSE
continues. Conditional early native-text streaming requires a clear reasoning
boundary and valid citations. Inference, malformed-output and capacity failures
remain explicit; the client does not retry automatically.

See OPTIMIZATION_LOG.md and eval-results/optimization-2026-11-26 for measured
results, rejected candidates and remaining semantic-quality limitations.
