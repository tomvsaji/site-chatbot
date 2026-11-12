# Current chatbot logic

The model is post-trained LiquidAI/LFM3-1.7B, Q4_K_M, served by pinned llama.cpp
b11960. The server permits 8192 context tokens, 640 output tokens, two CPU
threads, and one active inference request.

1. **Server admission:** enforce strict HTTP/JSON framing, approved browser
   origins, body/header limits, read/write deadlines, bounded HTTP workers,
   trusted-proxy client identity, per-client/global quotas and optional
   challenge policy. Turnstile is currently disabled. CORS is not authentication.
2. **Conversation context:** a private secure cookie selects server-side SQLite
   memory. Provide a small recent-message window (target two exchanges), a
   rolling summary capped at 192 generated tokens, and an exact first-message
   anchor. Original older messages remain searchable. Client-supplied history
   never replaces private memory.
3. **LLM decisions:** the model decides whether to answer or call a search tool,
   chooses the query and may refine it. There are no article-name or topic
   keyword routes. A fixed maximum of three searches and a time budget prevent
   open-ended execution.
4. **Site tool:** search_site embeds the query with all-MiniLM-L6-v2, queries
   Qdrant, runs BM25 keyword search, and fuses rankings. Return small passages
   (target 100 lexical tokens), titles, headings, URLs and citation IDs.
5. **Memory tool:** search_memory retrieves only this session's archived
   passages using vector/keyword search or model-selected chronological order.
   It is offered only when older history exists outside the configured recent
   tail. Memory describes prior discussion; it is not independent site evidence.
6. **Tool loop:** the server executes only the allowed read-only searches,
   appends native tool results and asks the model again. The model writes the
   answer; code controls scope, resources, token budget and citation IDs.
7. **Output and persistence:** SSE progress streams while answer text is buffered.
   Remove accidental reasoning, check citations, normalize citation formatting,
   attach links, emit the validated answer and save completed exchanges. Invalid
   citations get at most one correction within the existing budgets. Unexpected
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
