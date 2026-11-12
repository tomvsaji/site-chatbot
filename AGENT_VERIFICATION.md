# Agent/memory verification — 2026-11-10

This replaces the fixed keyword/topic RAG pipeline; it does not change the chat
model. See `AGENT_SETUP.md` for algorithms, runtime and operating instructions.
The model chooses `search_site` and `search_memory` calls through llama.cpp's
native tool protocol. The only exposed actions are read-only searches.

A separate 8192-token llama.cpp instance and app were tested on this host using
`compose.agent-test.yaml`, a separate Qdrant collection and a separate memory
volume. The public chatbot remained on its original service during testing.
The smaller corpus chunks yielded 131 passages, all <=100 lexical tokens.
The embedding model remains MiniLM. The model's queries were observed directly
in isolated-service diagnostic logs, including "Tom specializes in", "SQL
agent" and "Building Reliable SQL Agents"; no Python article aliases selected
them.

Initial replay (`eval-results/agent-conversation.jsonl`) successfully performed
site searches and SQL/article answers, but exposed stale citation failures on
three turns. Those failures are preserved. Subsequent changes strip old
citation markers from assistant context, normalize "citation N" wording, and
permit one model-driven correction within the same search/time budget.

The corrected summary (`agent-memory-retest.jsonl`) described the actual
conversation rather than unrelated website search results, without making a
site-search call. It took 62.5 seconds in that test. A factual years-of-experience
follow-up searched the site and took 71.2 seconds; it exposed the citation-wording
format issue, which is now normalized. That response also speculated about a
longer work period: factual precision remains a model limitation.

Initial semantic recall of "first question" failed, demonstrating that nearest
neighbors are not a chronological index. Memory search now offers explicit
oldest/newest ordering and role filters, selected by the model, with exact
first-question metadata and per-session message numbers. This is not keyword
routing; it gives the model a suitable retrieval operation for temporal requests.
The originals remain stored after rolling-summary compression. An inspected
summary was 726 characters while all 14 completed messages remained archived.

31 unit/HTTP tests pass, covering tool execution and allowlisting, query
validation, malformed streams, inference search/output bounds, session limits,
context-token trimming without orphaning tools, private archive scoping,
chronological recall, summary/original preservation, expiry/deletion, secure
cookies, HTTP origin restrictions and reset-session behavior. JavaScript,
Python syntax and production/development Compose configuration checks pass.
Legacy tests tied to the removed keyword routes were replaced, rather than
retaining those routes to satisfy obsolete tests.

Observed isolated-service cgroup peaks so far: model 1,779,748,864 bytes
(~1.66 GiB), app 452,509,696 bytes (~432 MiB) after reducing site embedding
batches to 16. Independent peaks are not a simultaneous stack peak and include
charged cache. The former large embedding batch reached ~729 MiB. Runtime
limits remain 2500 MiB inference and 768 MiB app; the context increase fits in
the existing limits in these tests. Model-controlled retrieval adds inference
rounds; compression also costs a model call. It is not a speed improvement claim.

No new full 25-turn benchmark score is claimed. Existing LFM3/Qwen scores belong
to the older retrieval/prompt architectures. Model semantic citation correctness,
relevance, and instruction following remain imperfect; validating citation IDs
cannot establish entailment. Streamed drafts are provisional until the final
answer passes format/citation checks.


## Final deployment and live checks

Deployed at https://chat.srv1619516.hstgr.cloud with 8192-token runtime/context,
131 site chunks, two recent exchanges as the target verbatim tail, a <=192-token
rolling summary and a session-scoped hybrid memory archive. One extra exchange
may remain verbatim until the next compaction batch. A stable first-user-message
anchor supplements the lossy summary. `search_memory` is not offered in a fresh
or very short session, preventing empty-archive searches; this is capability
availability, not topic/keyword routing.

Final targeted archive fixture: an older codeword (`nebula-417`) was deliberately
omitted from BOTH recent messages and a compressed-summary fixture. LFM3 emitted
an actual `search_memory` call and recovered it correctly in 36.041 seconds.
The first-message test eventually concluded with the correct exact question via
the chronology anchor, but included a contradictory preface and a false claim
about a memory call. That is a remaining model reliability issue, not a claimed
perfect pass. Evidence: `eval-results/agent-archive-proof.jsonl`.

Live HTTPS tests verified streamed site retrieval with source links, same-cookie
conversation summarization, and Clear. After withholding an unavailable memory
tool, a fresh factual answer took 36.806 seconds, first text at 28.060 seconds,
with one site search and no memory searches. The earlier summary took 16.606
seconds, first text at 13.502 seconds, and described the actual preceding exchange.
Clear returned 200, rotated the cookie, and database checks confirmed zero old
session/message/passage records (`agent-clear-proof.json`). The final test also
cleared its synthetic production conversation. Secure/HttpOnly/SameSite flags
were checked over actual HTTPS. Normal operation does not log search queries.

An actual isolated-inference outage produced the expected SSE error, while the
public service stayed running. All isolated test containers were stopped/removed;
the isolated test volume/collection remain separate for inspection. Existing
non-chatbot services remained running. No additional model weights were installed.

31 final unit/HTTP tests passed, plus Python/JavaScript/shell/Compose and whitespace
checks. Production/source files match for the runtime modules, UI and configuration.
Observed production model peak was at least 1,692,385,280 bytes (~1.58 GiB), app
467,935,232 bytes (~446 MiB); independent cgroup peaks include cache. Runtime
limits remain unchanged. Tool use and compression introduce extra inference
work, so the new architecture is not claimed to be faster than the fixed pipeline.

Deployment files before this change are backed up at
`/home/codex/site-chatbot-backup-agent-20261110`. Source changes are local and
have not been committed or pushed; deploying unchanged upstream main would
restore the prior pipeline. Setup and memory lifecycle are in `AGENT_SETUP.md`.
