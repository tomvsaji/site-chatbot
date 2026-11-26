# Lightweight website assistant

`CHAT_MODE=simple` is the default. `CHAT_MODE=agent` retains the old tool loop
for experiments. Both keep validation, private cookies, per-client/global quotas,
optional Turnstile and the one-model admission gate.

1. Validate request/origin/framing and enforce quota/challenge policy.
2. Exact normalized FAQ aliases return editorial answers with sources and
   `response_mode=curated`. They store the exchange without embeddings and
   don't acquire the inference slot, so FAQs work while generation is busy.
   There is no fuzzy matching or cross-user generated-answer cache.
3. Other requests acquire one inference slot or get 503/Retry-After.
4. Read four recent messages, capped at 400 characters each in the prompt.
   Referential follow-ups include the previous user question in their query.
   Do one hybrid search for three passages. Irrelevant results abstain without
   generation. Relevance thresholds remain heuristics, not proofs of scope.
5. Measure rendered context with template/tokenize endpoints. Generate once,
   without tools, with a 256-token cap and a prompt requesting at most 80 words.
   No search planning, citation repair, summaries or archive searches require
   additional generation calls.
6. Buffer text; remove closed reasoning prefixes; reject malformed, truncated
   or incorrectly numbered citations. Uncited output abstains. Citation ID
   validation doesn't prove factual accuracy or semantic entailment.
7. Emit answer, sources, response_mode and done. Store plain turns without
   embedding them. Existing archives remain until deletion/expiry but aren't
   searched in simple mode.

The standalone UI labels curated/generated answers. The separate Netlify
frontend should render response_mode too; this repository doesn't contain it.
SSE final/done and sources remain compatible with clients ignoring the new field.

## Resources and limitations

- MAX_ANSWER_TOKENS=256 (64–640 allowed).
- EMBEDDING_THREADS=1 in both modes.
- LLM_CPUS=1.0 sets production model CPU quota. It doesn't exclusively reserve
  a core; app, retrieval and other services still consume CPU.
- LLM_THREADS=1 sets generation/batch threads to match the default CPU quota.
- Production global hourly attempts default to 30. Existing .env values override
  defaults; review runtime configuration explicitly during rollout.
- One inference slot, no unbounded queue and no automatic retries.
- Private storage TTL, 40-question session cap and Clear remain. Simple mode
  cannot recall all old turns or promise a full-conversation summary.
  Start a fresh session when switching back to agent mode: simple-mode turns
  have no archive embeddings, so archive search won't recall those turns.
- Quotas reset on restart. Busy responses, failed challenges and FAQs still
  consume request quota. Fairness and Turnstile UI integration remain separate
  hardening work; this isn't an abuse-proof deployment.
- The 60-second generation socket timeout is an inactivity timeout, not a
  guaranteed total execution deadline. CPU quota bounds CPU rate, not total work.

See benchmarks/README.md for measurements and rollout gates.

## Per-response metrics

Successful agent responses include a public `metrics` object in the final SSE
payload (and JSON response). Timings use a monotonic clock per request, isolated
with ContextVar. `elapsed_ms` covers agent execution through memory persistence;
`model_calls` and `model_time_ms` include all generation calls, including tool
selection, citation correction and memory compression. Template rendering and
context tokenization are not generation calls. `search_calls` and `search_time_ms`
cover attempted allowed search_site/search_memory tool execution.

llama.cpp streams request `stream_options.include_usage=true`. Both usage-only
empty-choice chunks and usage attached to the finish chunk are accepted. Token
counts are summed once per generation call from runtime usage; no text-length
estimates are used. If any call lacks valid complete usage, all token totals and
throughput are null, usage_complete=false, usage_source=unavailable. Model identity,
call counts and timings remain available. Overall throughput divides total generated
tokens by full generation-call wall time, including prompt processing and waits;
it is not decoder-only speed. These counters are ephemeral, contain no prompts,
credentials or private runtime URLs, and do not alter retrieval or session state.

In simple mode these counters cover the one hybrid search and the one generation.
Curated FAQs report zero search/model calls, measured elapsed time, and unavailable
token usage because no model was called. The legacy agent retains its metrics.

