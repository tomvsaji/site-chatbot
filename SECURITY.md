# Native widget API security and release checklist

This is a local, undeployed hardening change. Review and release separately.
The API is public; CORS and Origin checks are **not authentication**. Scripts can
omit or forge Origin. No API secret belongs in the portfolio browser bundle.
ChatGPT/Codex authentication and billing are unrelated and unchanged.

## API contract

The native Next.js widget on `https://tomvsaji.com` calls
`https://chat.srv1619516.hstgr.cloud/api/chat/stream` with POST and
`Content-Type: application/json` (UTF-8):

```json
{"question":"What has Tom built?","history":[],"turnstile_token":"optional-token"}
```

Omit `turnstile_token` when challenges are disabled. Question must be a string,
trimmed length 1–800 by default. Body must be a JSON object; no scalar, array,
NaN, invalid UTF-8, or coerced question values. History must be a list of objects
with role `user`/`assistant` and string content. As before, only the last eight
messages, each truncated to 800 characters, reach retrieval. The entire encoded
request, including history and token, must fit `MAX_BODY_BYTES` (10,000 default).
Existing sources, retrieval, LFM3 prompts, generation, and same-model retry are
preserved. The JSON `/api/chat` endpoint has the same protections.

SSE still emits `status`, validated `claim`, `final` with existing sources, and
`done`; generation errors emit a generic `error`. Before SSE starts, failures
are JSON: 400 invalid input/framing, 403 origin/challenge rejection, 413 body
size, 415 media type, 417 Expect, 429 quota, 431 headers, or 503 capacity/service
failure. HTTP 429/503 have integer-seconds `Retry-After`; errors after HTTP 200
must be handled as SSE errors. Obtain a fresh challenge token for each retry.
Never display raw provider errors. All responses close their connection; SSE
runs until completion, error, or disconnect. There is no iframe requirement.

Origins are exact entries in `ALLOWED_ORIGINS`, currently the chat hostname,
`https://tomvsaji.com`, and `https://www.tomvsaji.com`. There are no wildcards or
Netlify preview origins. OPTIONS permits POST and Content-Type. Missing Origin
is still allowed for existing non-browser clients. Duplicate Origin headers
are rejected. Origin does not change quota identity.

## Proxy trust: configure both boundaries

`TRUSTED_PROXY_CIDRS` defaults to empty: **ignore all forwarding headers**.
Behind Traefik this safely groups visitors under the proxy's socket IP until
trust is deliberately configured; it can therefore rate-limit the whole site.
Do not enable public traffic under the assumption that per-visitor identity is
already configured.

Before release, the operator must determine the actual socket peer address seen
by the app on its private Traefik-to-app connection. Configure only Traefik's
verified address(es), ideally stable `/32` IPv4 or `/128` IPv6 entries. If a
small dedicated subnet is necessary, isolate it so that only trusted proxies
can reach the app from it. Do not guess a bridge CIDR, trust every container,
trust all RFC1918 networks, or use `0.0.0.0/0` or `::/0`. Keep the app port
loopback/private and block alternate public paths. Recheck peers after network
recreation. Invalid CIDR configuration stops startup.

At Traefik's public `websecure` entrypoint, keep
`forwardedHeaders.insecure: false`. For direct Internet-to-Traefik traffic,
leave `forwardedHeaders.trustedIPs` empty so client-supplied XFF is not trusted.
If there is a real upstream CDN/load balancer, set `trustedIPs` to **only that
upstream's maintained egress ranges** and firewall bypasses; do not add those
ranges to the app unless they actually connect directly to it. Also keep
`proxyProtocol.insecure: false`; do not enable PROXY protocol unless needed
and restricted to verified senders. These are Traefik **static entrypoint**
settings, not app Compose labels. No Traefik configuration was changed or
inspected on the live server in this task.

The app considers only X-Forwarded-For from a trusted immediate socket peer.
It validates the entire chain (max 32 IP literals), walks right-to-left through
trusted proxies, and stops at the first untrusted hop. Forged values to its
left cannot select a client. Invalid entries, empty hops, ports, brackets, or
IPv6 zone IDs fall back to the peer identity. Duplicate XFF fields are joined
in received order. `Forwarded`, X-Real-IP, and CF-Connecting-IP are ignored.
IPv4-mapped IPv6 is canonicalized; IPv6 client quotas group addresses by /64.
Do not trust a forwarding chain solely because it is syntactically valid.

See [Traefik EntryPoints](https://doc.traefik.io/traefik/routing/entrypoints/)
for the version-specific entrypoint settings and forwarding trust semantics.
The actual installed Traefik version and topology require operator validation.

## Quotas and resource controls

| Setting | Default | Effect |
| --- | --- | --- |
| `REQUESTS_PER_MINUTE` / `REQUESTS_PER_HOUR` | 3 / 20 | Sliding client windows |
| `MAX_RATE_LIMIT_CLIENTS` | 4096 | Maximum active identity entries |
| `GLOBAL_REQUESTS_PER_MINUTE` / `GLOBAL_REQUESTS_PER_HOUR` | 10 / 100 | Shared across all client IPs, origins, both routes |
| `MAX_MODEL_REQUESTS` | 1 | Existing nonblocking inference semaphore; retain 1 on this host |
| `MAX_QUESTION_CHARS` | 800 | Trimmed question characters |
| `MAX_BODY_BYTES` | 10000 | Encoded request body bytes |
| `MAX_HTTP_CONNECTIONS` | 32 | Accepted handler workers, before body parsing |
| `REQUEST_READ_TIMEOUT` | 15 seconds | Absolute header/body deadline plus socket read timeout |
| `RESPONSE_WRITE_TIMEOUT` | 15 seconds | Socket write timeout, including SSE slow readers |
| `TRUSTED_PROXY_CIDRS` | empty | Trusted immediate proxy peers and chain hops |

Quota entries expire after one hour of inactivity. Each deque is bounded by
its hour allowance. A full table rejects new identities with 429 until entries
expire; active clients are never evicted to make room for attacker churn.
The global budget prevents different real IPs from multiplying inference
admissions. Reservations happen before Turnstile and model admission: failed
challenges and busy responses conservatively consume budget too. A request can
reserve up to the existing two sequential generation attempts; these do not
count as separate HTTP requests. Health/static/OPTIONS are outside inference
quotas but share connection bounds. No request queues for a free model slot.

The server admits at most 32 workers and a bounded listen backlog (32), with
at most one input timer per active worker. Headers plus request line have a
16 KiB aggregate parsing budget (Python also bounds individual request lines
and header counts). Unsupported Transfer-Encoding, duplicate Content-Length
(including equal values), non-decimal/negative lengths, and oversized bodies
are rejected before reading the body. Expect: 100-continue is rejected. Every
response closes the connection, including early rejection with unread input,
so leftover bytes cannot become a subsequent request. Keep-alive is disabled.
The absolute read timer is cancelled once input is consumed; model computation
and SSE are not constrained by that input deadline. Existing upstream model
and retrieval timeouts remain unchanged.

These quotas are **in-memory, single-process, and reset on restart**. Multiple
workers/replicas each get a separate allowance. NAT users and IPv6 /64 users
share quotas. Table exhaustion and global budget exhaustion can deny legitimate
users. These controls bound local work; they do not provide distributed DDoS
protection, fairness, IP reputation, or network bandwidth protection. Retain
upstream connection/rate controls, firewalling, and monitoring. Socket timeouts
for verification/model services are inactivity timeouts, not guaranteed total
wall-clock limits (including DNS); a deliberately trickling upstream can occupy
a bounded worker longer. Do not raise resource settings casually.

Configure Traefik request read/idle timeouts, connection limits, and an app-only
in-flight limit to suit this VPS. Avoid response buffering and do not use a
short total write timeout that terminates legitimate SSE generation. Validate
stream duration and flush behavior through the real proxy before rollout.
App access logging omits raw requests and exception details to protect tokens;
configure proxy logs to redact request bodies, tokens, and query strings too.
Existing CSP, frame denial, referrer policy, and nosniff headers are retained.

## Optional Turnstile

| Setting | Default |
| --- | --- |
| `TURNSTILE_REQUIRED` | `false` |
| `TURNSTILE_SECRET_KEY` | empty, server-side only |
| `TURNSTILE_EXPECTED_HOSTNAMES` | `tomvsaji.com,www.tomvsaji.com` |
| `TURNSTILE_EXPECTED_ACTION` | `site_chat` |
| `TURNSTILE_TIMEOUT` | 5 seconds |

When optional, an omitted/null token keeps the existing challenge-free API.
A supplied token is verified. When required, missing key/hostname/action
configuration returns 503; missing/invalid token returns 403. Siteverify uses
HTTPS, a socket timeout, and a 16 KiB response cap. Only boolean `success: true`
with an exact allowed hostname, exact action, and no provider error codes is
accepted. Cloudflare enforces single use; expired/reused tokens fail. Timeouts,
network failures, invalid JSON, and oversized responses fail closed with 503.
No token, secret, or raw upstream response/error is logged or returned.
See [Cloudflare server validation](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/).

Production needs a real Cloudflare Turnstile widget with allowed website
hostnames, its matching **server secret**, and its public **site key**. Keep
`TURNSTILE_SECRET_KEY` in protected runtime configuration, outside Git and the
browser. Only `NEXT_PUBLIC_TURNSTILE_SITE_KEY` goes into the independently
implemented Next.js frontend. Render with action `site_chat`, obtain a fresh
token for each submission, send it as `turnstile_token`, and reset it after
use/failure. The Next.js site's CSP must allow the official Turnstile widget
resources if enabled. No real keys are supplied here; do not enable production
challenges with placeholders. Challenge-free compatibility remains the default.

The standalone page served at `/` uses `static/app.js`, which has **no
Turnstile flow** and sends no `turnstile_token`. `TURNSTILE_REQUIRED=true`
therefore blocks chat submissions from that UI (403 for a missing token when
server configuration is valid, or 503 when configuration is missing). The
standalone page and same-origin/no-Origin requests have no challenge exemption;
required verification cannot be bypassed through that route. The main native
Next.js frontend handles the token flow independently. Enabling required mode
does not add that flow to the standalone page.

## Production checklist and rollback (operator action, not executed)

1. Review this branch and tests; preserve the dirty live checkout separately.
   Do not overwrite it or deploy this uncommitted workspace. No release was
   attempted in this task.
2. Record the known-good release and runtime configuration, securely back up
   server secrets, and arrange a separate reviewed deployment window.
3. Verify the actual Traefik peer/topology and static forwarding policy above.
   Set narrow `TRUSTED_PROXY_CIDRS`, retain private ports, and configure upstream
   connection/time limits. Keep existing retrieval/model settings unchanged.
4. Set explicit per-client/global budgets and leave inference capacity at one.
   Validate the deployment manifest with the deployment tooling in that window.
5. Keep `TURNSTILE_REQUIRED=false` until real keys and frontend token handling
   have been tested together. Test optional verification first, then explicitly
   enable required mode if desired. Test hostname/action mismatch, fresh-token
   success, reuse rejection, key removal, and verification outage behavior.
6. Through the real HTTPS endpoint, verify exact-origin preflight, SSE
   status/claim/final/done and source format, 429/503 Retry-After, forged XFF
   resistance, slow-request rejection, and health. Do not send secrets in test
   output. Confirm no proxy buffering and no stream truncation at normal model
   latency. Monitor bounded resource use and legitimate-user rejection rates.
7. If challenge activation fails, disable required mode and remove the frontend
   challenge/site-key activation together; optional requests with supplied
   tokens still verify. If the API itself regresses, restore the recorded
   known-good application release and runtime settings through the established
   release procedure. A process restart resets quotas. Do not undo private-port
   or proxy trust restrictions to make a failing rollout appear healthy.

This change does not add authentication, change billing, implement the native
frontend, change the model/retrieval stack, add a shared quota backend, or modify
the live `/srv/site-chatbot` checkout.
