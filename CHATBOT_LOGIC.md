# Current chatbot logic

The model is post-trained LiquidAI/LFM3-1.7B, Q4_K_M, served by pinned llama.cpp
b11960. The server permits 8192 context tokens, 640 output tokens, two CPU
threads, and one active inference request.
The optional runtime RAM prompt cache is capped at 256 MiB by default through
`LLM_PROMPT_CACHE_MIB`, separately from the active model/KV/context allocation.
Development Compose uses this production model service over its external network.

1. **Server admission:** enforce strict HTTP/JSON framing, approved browser
   origins, body/header limits, read/write deadlines, bounded HTTP workers,
   trusted-proxy client identity, per-client/global quotas and optional
   challenge policy. Turnstile is currently disabled. CORS is not authentication.
2. **Conversation context:** a private secure cookie selects server-side SQLite
   memory. Keep every unsummarized exchange until the rendered prompt exceeds
   the default 3,600-token memory budget, then summarize older turns while
   retaining the latest two exchanges. Summary generation permits 192 tokens;
   an exact first-message anchor survives separately. Originals remain searchable. Client-supplied history
   never replaces private memory.
3. **LLM decisions:** the first model request produces a strict JSON search plan;
   the same model chooses website evidence or private conversation memory and
   writes its own query. Code validates the bounded plan and marshals it to the
   allowlisted tool executor and native tool-call history. This first step uses
   structured model output, not an API-native function call. The current question
   is the last user message; two short recent messages are untrusted reference
   data. The exact first-question anchor stays in memory-tool/answer context.
   Later requests allow a refinement or an answer. There are no article-name or
   topic keyword routes. The default maximum is two searches (configurable),
   normally at most three model calls plus one bounded citation-repair retry;
   token-budget compression can add its own call when needed.
   The initial JSON planning step permits 192 tokens and a free-form query
   of 1–240 characters; answer generation retains its 640-token allowance.
4. **Site tool:** search_site embeds the query with all-MiniLM-L6-v2, queries
   Qdrant, runs BM25 keyword search, and fuses rankings. The model also selects
   a document group: `profile` for general identity/career/client overviews,
   `writing` for named articles or the writing catalog, or `all` when specific
   technical experience/implementation may need both profile and first-hand
   writing. Both retrievers filter source metadata before ranking/fusion;
   the backend never selects a group from query keywords. Native refinements
   default to `all` if no group is supplied. Retrieve eight candidates
   and return four short excerpts with titles, headings and citation IDs.
   Full source metadata and URLs remain on the server for the final sources.
5. **Memory tool:** search_memory retrieves only this session's archived
   passages using vector/keyword search or model-selected chronological order.
   Both tool schemas remain available and stable across turns. Memory describes
   prior discussion; it is not independent site evidence.
   When all history is visible (no archived summary), return only compact
   visibility/count/first-question metadata instead of re-embedding that history.
6. **Tool loop:** the server executes only the allowed read-only searches,
   appends native tool results and asks the model again. The model writes the
   answer; code controls scope, resources, token budget and citation IDs.
   After tool results, a short user control message repeats the original question,
   brevity target and appropriate evidence rules. At search-budget exhaustion,
   remove tool schemas from the final request and state no tools are available;
   historical native tool exchanges remain intact.
7. **Output and persistence:** SSE progress streams while unmarked answer text
   remains buffered. After an explicit closing reasoning tag, complete sentence
   prefixes with valid current citations can stream provisionally. A reset clears
   provisional text on tool rounds, truncation or citation repair. Site prefixes
   wait for at least one current valid citation. Remove accidental reasoning, check citations, normalize citation formatting,
   attach links, emit the validated answer and save completed exchanges. Invalid
   or missing site citations get at most one correction within the existing
   budgets. Any site lookup requires current citations or the exact source-free
   abstention “I couldn't find that in Tom's published site data.” A repeated
   uncited noncanonical reply fails clearly. Memory-only replies need no site
   citations. Invalid tool calls never count as successful retrieval. Unexpected
   internal failures are sanitized; safe session/context-limit errors stay clear.
8. **Limits and cleanup:** default 40 questions per conversation, seven-day
   inactivity retention, bounded stored sessions, and 3/minute plus 20/hour
   per-client limits (global 10/minute and 100/hour). Clear deletes that session
   and rotates its cookie. Limits are in-process and reset with the app.

The standalone chat hostname supports same-origin private memory. A frontend
on another domain needs a coordinated same-origin proxy/session design; CORS
permission alone does not enable cross-site memory cookies. See SECURITY.md.

This is a bounded tool-using RAG agent, not an autonomous general-purpose agent.
It cannot run shell commands, browse external sites, access other sessions or
change documents. Citation validity does not prove semantic correctness: LFM3
can still misinterpret evidence or conversation memory.

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

Prompt-cache metadata is independent of token-usage completeness. Valid runtime
`usage.prompt_tokens_details.cached_tokens` is preferred; pinned-runtime
`timings.cache_n`/`prompt_n` is accepted only with a consistent prompt-token
denominator. `cached_prompt_tokens`, `cache_prompt_tokens` and the weighted
`prompt_cache_reuse_ratio` cover all generation calls, including compression.
If any call lacks valid cache metadata, `prompt_cache_complete=false` and cache
totals/ratio are null. Explicit zero means no reuse reported; missing telemetry
means unavailable. This measures prompt/KV reuse, not an answer cache.
