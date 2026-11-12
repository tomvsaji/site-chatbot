# LFM3 migration — 2026-11-09

Production: https://chat.srv1619516.hstgr.cloud . Deployment directory:
`/srv/site-chatbot`; working source: `/home/codex/site-chatbot`.
No applicable AGENTS.md files were present in these paths or their ancestors.
Normal execution failed before launching with a bwrap permission error; elevated
execution provided actual host access. No GPU was detected; the host has two
AMD EPYC virtual CPU cores and 7.8 GiB RAM, with no swap.

Official compatibility was checked before installation:
https://huggingface.co/LiquidAI/LFM3-1.7B and
https://docs.liquid.ai/deployment/on-device/llama-cpp .
Selected official post-trained Q4_K_M GGUF, immutable revision and checksum;
llama.cpp b11960 (0.8.0-dev, commit 8642541b2), digest-pinned Docker image.
See README for exact versions, commands, configuration and deployment steps.

The newer working checkout's retrieval, SSE and citation validation were brought
to production. Site data was byte-identical between checkouts and was preserved.
Static design, source corpus, origin controls, rate limits and site hostname
were preserved. Existing application has no login. Credentials remain on the
server; no model credentials are needed. No external fallback provider or model
selector exists. Old Qwen/SmolLM-specific prompt handling is removed. One
inference slot, two CPU threads, 4096 context, bounded eight-message history
and bounded output keep work predictable. MiniLM embeddings, embedding cache,
Qdrant volume, hybrid retrieval, source links and unsupported-question abstention
remain. Truncated streams and malformed completion responses now fail clearly.

Deployment includes an idempotent checksum-verifying model download script and
updated restricted SSH deployment hook. Previous deployed files and environment
are backed up at `/home/codex/site-chatbot-backup-20261109` (server only).
Changes are local and not published to upstream GitHub; deploying upstream's old
main would overwrite them. The separate `/home/codex/site-chatbot-ghcr` checkout
is unused and was not promoted into production.

After successful real LFM3 chat and follow-up tests, removed the unused
`site-chatbot_model-cache` Docker volume, confirmed to have no container mounts.
Only chatbot-owned old weights were removed (about 19 GB):

- ibm-granite/granite-4.0-1b-GGUF
- ibm-granite/granite-4.0-micro-GGUF
- ggml-org/gemma-3-1b-it-GGUF
- ggml-org/gemma-3-4b-it-GGUF (including cached projector)
- Qwen/Qwen3-4B-GGUF
- AtomicChat/Qwen3.5-4B-GGUF
- bartowski/Llama-3.2-3B-Instruct-GGUF
- unsloth/Phi-4-mini-instruct-GGUF
- ggml-org/SmolLM3-3B-GGUF

Old weights are no longer available for offline rollback. The backup source
still records old model names as historical configuration.

Verification evidence is in `eval-results/lfm3-*`. Unit tests: 26 passed.
Production and development Compose configuration, deployment shell syntax,
JavaScript syntax and Git whitespace checks passed. Real HTTP tests verified
chat, follow-up context, SSE claim/final/done events, invalid history (400),
untrusted Origin (403), rate limiting (429), stopped inference (JSON 503 and
SSE error), and inference recovery. Public TLS health reports the expected model
and 89 indexed document chunks. No browser visual automation was available;
static design files were preserved except bounded client history storage.

Initial three real requests took 17.675 s, 22.006 s and 16.708 s end to end.
First streamed cited claim arrived at 16.502 s. Runtime logs showed about
68–75 prompt tokens/s and 17–18 generated tokens/s for those requests. These
are small-sample host observations, not capacity guarantees.
Observed model cgroup peak reached at least 2,002,923,520 bytes (1.87 GiB)
during initial loading/inference; app peak 674,660,352 bytes (643 MiB),
Qdrant peak 143,028,224 bytes (136 MiB). Independent peaks are not a simultaneous
stack peak. Cgroup memory includes charged file cache; Docker stats subtracts
some cache and was lower after model restart. Other applications remain on the
host, and their memory is excluded from these chatbot figures.

Answer quality is separate from runtime success. The initial 320-token suite
exposed incomplete answers, unexpected abstentions and one manual streamed
claim attributed to an unrelated passage. Citation validation verifies shape
and source IDs, not semantic entailment. Two long requests hit exactly 320
generated tokens, so explanatory requests now permit up to 512 tokens;
simple factual requests remain capped at 320. Context remains bounded at 4096.

The initial full 25-turn suite passed 17/25 (68%), mean rubric score 0.76,
median 18.4 s and p95 33.4 s. These figures describe the initial uniform
320-token configuration. The final 512-token explanatory allowance is retested
separately in `eval-results/lfm3-final-focused.json`; it is not a full-suite
rescore. Unsupported consulting-price requests returned unrelated site facts
in the initial suite, so factual relevance/abstention remains a known limitation.

The 512-token focused retest initially passed 4/7, median 35.6 s, p95 52.6 s.
Concise per-claim instructions were then added and deployed. A final public HTTPS
streamed SQL follow-up returned all six cited steps, first claim at 26.201 s,
completed in 43.502 s (377 generated tokens). Its existing rubric result is True
with score 1.0. Evidence: `lfm3-final-public-walkthrough.json` and
`lfm3-final-walkthrough-score.json`. No full-suite score is claimed for this final
prompt adjustment. Broader relevance and citation entailment issues remain;
the replacement is deployed but has not met the complete quality regression bar.
