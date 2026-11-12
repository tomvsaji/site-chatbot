# Chatbot branch consolidation — 2026-11-12

The formerly uncommitted live LFM3 agent/memory implementation is preserved in
commit d9ba7e7, based on local main's earlier RAG commit 1691da2. Integration
retains the ancestry of both open feature branches: native-widget security
cc24199 and GHCR publishing 6682cba. No feature history is discarded.

Security integration resolves the nine overlapping files without restoring
keyword/article routes or client-owned history. The new bounded HTTP server
validates framing, JSON/encoding and read/write deadlines; applies trusted-proxy
identity, bounded client/global quotas and optional challenges before inference;
and wraps the existing native tool loop. Reset is protected identically to chat.
Explicit application errors have an AgentError type; arbitrary exceptions are
sanitized. Native SSE delta events, source links, private cookies and the memory
archive are preserved. All 62 combined tests passed at the security merge.

The GHCR workflow publishes tested commit-specific images, provenance and SBOM
on main. Production still builds the exact source commit on the VPS; switching
the VPS to consume the published digest is separate work. Feature branches run
CI; publishing/deployment remain restricted to main. The redundant develop
branch is a cleanup candidate only after current refs are checked again.

Proxy evidence: the live app observed peer 172.16.3.1; the Docker network gateway
is 172.16.3.1 and Traefik uses host networking. Installed Traefik 3.6.14 reports
forwardedHeaders.insecure=false, trustedIPs empty and proxyProtocol.insecure=false;
its configured arguments do not override these. Trust only 172.16.3.1/32 on this
host, and reverify after network/proxy changes. App binding remains loopback-only.
Turnstile remains disabled; no keys, credentials or authentication were changed.

## Current chatbot logic

1. Validate the request and origin; enforce body/header/read/write/connection
   bounds, trusted client identity, per-client and global quotas, optional
   challenge policy, and one-model concurrency.
2. Resolve private memory from the cookie. Provide recent exchanges, a compact
   rolling summary, and an exact first-user-message anchor. Old messages remain
   archived with MiniLM vectors and keyword indexing.
3. LFM3 chooses its own tool calls: search_site queries MiniLM/Qdrant plus BM25;
   search_memory queries only this conversation, with optional chronological
   order. Memory search is not offered when no older history exists.
4. Execute only those allowed read-only tools, append native tool results, and
   let the model continue. Search count, elapsed time, actual prompt tokens and
   output length are bounded. No topic or article is selected by keyword routes.
5. Stream progress while buffering model text; remove reasoning prefixes, validate final citation IDs, attach published source
   links, and save completed exchanges. Compact old turns as needed; Clear
   deletes the private session. Defaults remain 40 questions and 7-day inactivity
   expiry. Summaries/earlier assistant claims are not independent site evidence.

The model remains LFM3-1.7B Q4_K_M on pinned llama.cpp b11960 with 8192 context,
640 output tokens, two CPU threads and one slot. Retrieval still uses MiniLM;
site chunks target 100 lexical tokens. Existing factual reliability limitations
remain. This integration does not claim a new full benchmark score.

The standalone chat UI supports its same-origin memory cookie. A native frontend
on another site needs a coordinated proxy/session design; old client-supplied
history is not silently imported into server memory. See SECURITY.md.

## Validation and rollout

Further combined-image and real-model results will be appended before release.
The original unrelated SECURITY_HARDENING_TODO.md and rollback backups are
preserved outside the integration commit. No branch is deleted before its
unique commits are reachable from the published consolidation.


Final pre-release validation: **66 unit/HTTP tests pass**, including new checks
that accidental reasoning never reaches client events or memory. Real-model
smoke on the corrected built image returned cited chat in 36.182 seconds and a
same-session summary in 19.397 seconds; no reasoning tag or prefix appeared in
the final summary. Clear returned 200 and rotated the cookie. Malformed input
and foreign origins were rejected. Stopping only the isolated inference service
returned a clear SSE error while production remained available. These are
functional checks, not a new answer-quality benchmark; the actual answer still
mischaracterized Invest Qatar as a news agency, so semantic grounding remains
imperfect. Actual test output is preserved under eval-results/integration-*.

The previous dirty production tree was backed up at
`/home/codex/site-chatbot-prod-backup-20261112`. Its runtime source was verified
byte-for-byte against d9ba7e7 and made clean at that preserved commit, without
rebuilding or changing the running service. The server-only environment now
prepares `TRUSTED_PROXY_CIDRS=172.16.3.1/32`; no secrets or challenge keys changed.
This gives automated deployment a valid native-agent rollback commit instead
of the old Granite baseline. Final main-branch CI/release status is available
on PR #3 and GitHub Actions.


## Restrictive-permission deployment correction

The first main release passed tests and image publication but its VPS source
build copied 0660 files from the checkout ACL, preventing UID 10001 from reading
app.py. The service was restored with the already-tested matching image; runtime
source bytes were verified against a4a87ae and the checkout/release record aligned.
A Dockerfile correction normalizes copied public-source readability before USER
appuser. A separate build fixture with 0660 files / 0770 directories reproduced
the host modes and confirmed app, agent, memory, all static files and knowledge
sources are readable as UID 10001. CI now builds under those restrictive modes
and runs the same non-root import/readability check, preventing this gap from
passing validation again. No private memory, .env or credentials enter the image.
