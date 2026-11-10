# Native widget security implementation report

Date: 2026-11-10 (UTC)
Workspace: `/home/hermes/projects/site-chatbot`
Branch: `feat/native-widget-security`
Baseline: `b6f4452` (`chore: preserve deployed source as development baseline`)
Session ID: `01a1c484-87be-7aa1-8f08-42fa07e3226b`

## Result and scope

Implemented and tested locally. No commit, push, publish, deployment, Docker
invocation, authentication change, or billing change was performed. No access
to or edits in `/srv/site-chatbot` were needed. No model weights, embeddings,
or Python packages were downloaded. No applicable AGENTS.md was found in the
workspace or its ancestor paths. The working branch was already correct and
the checkout was clean at the start of the original implementation. The focused
review correction pass started with that implementation already staged; it
preserved the index and baseline LFM3 changes, adding only unstaged corrections.

Changes:

- `app.py`: explicit proxy CIDR trust, right-to-left XFF parsing, mapped-IPv4
  normalization, IPv6 /64 quota keys, bounded client state, shared request
  budgets, retained model semaphore, strict input/framing checks, header/body
  limits, absolute input deadline, write timeout, bounded connection workers,
  generic public errors, optional server-side Turnstile verification.
- `tests/test_security.py`: 30 additional attack and regression tests.
  Existing tests retained; the original valid-XFF test now explicitly supplies
  its trusted proxy instead of assuming unconditional forwarding trust.
- `compose.yaml` / `.env.example`: 13 new settings with matching defaults.
  `compose.dev.yaml` wires resource settings but explicitly disables proxy trust
  and challenges and supplies no production secret.
- `SECURITY.md` and README link: API details, proxy boundaries, configuration,
  release prerequisites, Turnstile setup, rollback, and limitations.
- `test-logs/`: actual RED/GREEN and final command output.

The POST contract remains `{question, history, turnstile_token?}`. No browser
API secret is introduced. Exact CORS origins, sources, status/claim/final/done/
error SSE events, retrieval logic, LFM3 prompts, generation parameters, and
existing same-model retry are preserved. History retains its existing last-eight
messages/800 characters normalization. Invalid question types now fail instead
of being coerced. All HTTP responses close connections, including rejections
with unread bodies. Challenge-free behavior remains the default.

## Execution and test-first evidence

All commands below ran from the workspace using Python 3.14.9. The existing
unittest suite stubs `vector_store`, so FastEmbed/Qdrant packages are not needed
for these tests. No venv or system installation was necessary.

Baseline command, also captured to `test-logs/00-baseline.log`:

```bash
python3 -m unittest discover -s tests -v
```

Result: 25 tests passed (initial execution 0.010s; recorded baseline 0.011s).

For each RED cycle, new tests were written and executed **before** implementing
the behavior. The RED command was:

```bash
python3 -m unittest discover -s tests -p test_security.py -v
```

Each GREEN cycle ran the complete suite:

```bash
python3 -m unittest discover -s tests -v
```

| Cycle | RED log and result | GREEN log and result |
| --- | --- | --- |
| 01 identity/quotas | `01-identity-red.log`: 5 tests; 5 missing-feature errors | `01-identity-green.log`: 30 tests passed, 0.011s |
| 02 HTTP/resources | `02-http-red.log`: 13 tests; 16 subtest failures, 4 errors | `02-http-green.log`: 38 tests passed, 1.349s |
| 03 Turnstile | `03-turnstile-red.log`: 18 tests; 5 missing-feature errors | `03-turnstile-green.log`: 43 tests passed, 1.332s |
| 04 header/parser edges | `04-edge-red.log`: 24 tests; 2 failures | `04-edge-green.log`: 49 tests passed, 1.478s |
| 05 history compatibility/overload headers | `05-compatibility-red.log`: 25 tests; 2 failures | `05-compatibility-green.log`: 50 tests passed, 1.500s |

Logs are under `test-logs/`. Some RED tests contain multiple malformed-input
subcases; failure counts can exceed test-method counts. Cycle 04 exposed a
pre-header error-handler crash as well as the absent header budget. Cycle 05
restored the existing history trimming behavior after compatibility review and
completed security headers on worker-overload responses.

Original implementation verification (superseded by the correction checks below):

```bash
python3 -m unittest discover -s tests -v
# exit 0; 50 tests passed in 1.530s; test-logs/06-final-tests.log
python3 -m compileall -q app.py rag.py vector_store.py evaluate.py sync_site.py tests
# exit 0; test-logs/06-compile.log and test-logs/06-check-results.log
git diff --check
# exit 0; test-logs/06-diff-check.log and test-logs/06-check-results.log
```

A standard-library static consistency check also confirmed all 13 new
`compose.yaml` variable fallbacks match `.env.example`; its result is in
`test-logs/06-check-results.log`. This is not Compose/YAML runtime validation.

Coverage includes forged XFF, right-to-left chains, malformed/IPv6 addresses,
IPv4 mapping, IPv6 grouping, client-table exhaustion/recovery, identity churn
against the global budget, quota enforcement across both routes, exact CORS,
malformed bodies/types/UTF-8/JSON, duplicate and invalid Content-Length,
Transfer-Encoding ambiguity, oversized bodies/headers, duplicate Origin,
Expect rejection, unread-body closing, trickled headers/bodies, worker admission
and recovery, busy Retry-After, sanitized errors, semaphore release, and SSE
claim/source preservation beyond the input deadline. Turnstile coverage checks
configuration/token absence, hostname/action mismatch, non-boolean success,
duplicate-token rejection responses, timeout, malformed/oversized provider
responses, and HTTP integration before retrieval.

## Focused review corrections and verification

Two independently reported defects were reproduced before changing `app.py`:

- Allowed browser origins could receive HTTP 429/503 but could not read
  `Retry-After`. Allowed origins now receive
  `Access-Control-Expose-Headers: Retry-After` on JSON, SSE and OPTIONS responses.
  Foreign and absent origins receive no exposure header. Regression assertions
  cover rate limiting, challenge unavailability, model capacity and model errors.
- Python accepts headerless `GET /\r\n` as HTTP/0.9 with dict headers, causing
  `get_all` to raise. HTTP/0.9 now receives a framed generic HTTP 505 rejection
  before Message-only methods run. The real worker exits without `handle_error`,
  releases its connection slot and cancels/joins the input timer. Existing
  malformed-header/early-error and SSE tests still pass.

`07-review-red.log`: 30 security tests in 2.197s, five failures (including
subtests), no errors; the failures reproduced missing CORS exposure and the
HTTP/0.9 worker exception. `07-review-green.log`: all 55 tests passed in 2.014s.
Two earlier harness attempts are retained in `07-harness-initial.log` and
`07-harness-adjustment.log`: the sandbox denied socket buffer configuration and
half-close; daemon workers also needed tracked handles for bounded joins. The
final harness uses neither denied operation and makes workers joinable.

The blocked-writer regression uses actual Unix stream sockets and a nonreading
client, filling the real send buffer with a 4 MiB fixture for both JSON and SSE.
The existing write(2) adapter now honors socket timeouts using nonblocking writes
and select with an absolute send deadline. With `RESPONSE_WRITE_TIMEOUT=0.15`,
workers finish within the 1.5s join bound, connection/model semaphores recover,
input timers stop, buffered HTTP 200 bytes are observed, and a following request
succeeds. No live inference runs. `07-write-timeout-control.log` records an
in-memory negative control replacing the write deadline with 5s: both route
subtests fail specifically because workers remain blocked after 1.5s (one test,
two expected failures, 3.013s; control command exits 0). This confirms the test
detects a missing deadline. No production timeout logic needed changing.

The standalone `/` UI uses `static/app.js` and has no Turnstile flow. Required
mode blocks that UI; same-origin and no-Origin requests have no exemption. A new
regression confirms tokenless submissions to both routes return 403 before
retrieval/inference or Siteverify when required configuration is valid. The
main native frontend handles tokens independently; no frontend feature or
challenge bypass was added. README and SECURITY.md now state this limitation.

Final correction checks are recorded in `08-final-tests.log`, `08-compile.log`,
`08-diff-check.log` and `08-check-results.log`.

```bash
python3 -m unittest discover -s tests -v
# exit 0; 55 tests passed in 1.978s
python3 -m compileall -q app.py rag.py vector_store.py evaluate.py sync_site.py tests
# exit 0; no output
git diff --check
git diff --cached --check
# both exit 0; no output
```

### Separately reported verification (not rerun in this correction pass)

The parent independently ran the preceding 50-test suite and a real loopback
TCP smoke against the local implementation: POST SSE with a model fixture
returned HTTP 200 with status/final/done, foreign Origin returned 403, invalid
question type returned 400, and connections closed. That smoke used fixtures
for the model; it was not live inference or a deployed-backend test.

Separately, an independent CLI check against the **existing production API**
received real HTTP 200 SSE with claim/final/done events and a cited model answer
in 38.86 seconds; production-origin GET/OPTIONS CORS also worked. These are
reported checks of the already deployed service, not evidence that this new
backend code was deployed or tested in production. No new backend deployment
or deployed-backend test was authorized or performed.

## New configuration

| Variable | Default |
| --- | --- |
| `TRUSTED_PROXY_CIDRS` | empty: ignore forwarding headers |
| `MAX_RATE_LIMIT_CLIENTS` | 4096 |
| `GLOBAL_REQUESTS_PER_MINUTE` | 10 |
| `GLOBAL_REQUESTS_PER_HOUR` | 100 |
| `MAX_BODY_BYTES` | 10000 |
| `MAX_HTTP_CONNECTIONS` | 32 |
| `REQUEST_READ_TIMEOUT` | 15 seconds |
| `RESPONSE_WRITE_TIMEOUT` | 15 seconds |
| `TURNSTILE_REQUIRED` | false |
| `TURNSTILE_SECRET_KEY` | empty; server runtime only |
| `TURNSTILE_EXPECTED_HOSTNAMES` | tomvsaji.com,www.tomvsaji.com |
| `TURNSTILE_EXPECTED_ACTION` | site_chat |
| `TURNSTILE_TIMEOUT` | 5 seconds |

Existing per-client budgets remain 3/minute, 20/hour. The existing model slot
default remains one; retain that value for this VPS. The header budget is a
fixed 16 KiB and the listen backlog is 32. Quota reservations precede challenge
verification and model admission; failed challenges/busy responses consume
reservations. An admitted request can retain its existing second generation
attempt, so HTTP budgets do not directly count every model call.

## Unavailable checks and limitations

- The sandbox denies AF_INET socket creation and socket send syscalls. Initial
  attempts confirmed PermissionError; no approval bypass was attempted. HTTP
  tests use real Unix stream socket pairs, the real BaseHTTPRequestHandler
  parser, and an adapter using `write(2)` for sends. Connection admission tests
  skip only TCP-server initialization and use the actual worker/semaphore code.
  This correction pass did not run a listening TCP server or public HTTPS/
  Traefik checks; the separately reported checks above have distinct scope.
- PyYAML is unavailable. Docker was prohibited and not run. Compose runtime
  validation and container resource measurement remain release prerequisites.
- Model/Qdrant/embedding behavior is covered by existing lightweight logic
  tests, not a live retrieval or inference run. External model/retrieval and
  Cloudflare responses are stubbed in HTTP/security tests. No real Turnstile
  verification or production key was used in this implementation's tests;
  no production success of the new backend is claimed.
- Quotas are in-memory, single-process, and reset on restart. Replicas do not
  share budgets. IPv6 /64 and NAT grouping can affect legitimate users.
  Attacker churn cannot grow the client table or reset existing quotas, but can
  exhaust table/global capacity and deny legitimate traffic.
- These are local resource controls, not distributed DDoS protection. Bandwidth,
  edge connections, proxy buffering/timeouts, firewalling, and shared quotas
  require operational controls. Upstream socket timeouts are not guaranteed
  total wall-clock deadlines for DNS or a trickling upstream.
- With empty proxy trust behind Traefik, visitors share the proxy quota. Actual
  proxy peers must be verified before relying on individual visitor limits.
  Origin checks are browser policy, **not authentication**.

## Deployment prerequisites and rollback

Follow `SECURITY.md` in a separate authorized release. Determine actual app
socket peers and trust only Traefik's verified /32 or /128 addresses (or an
isolated narrow proxy-only subnet). At the public Traefik entrypoint, leave
forwardedHeaders.insecure false and trustedIPs empty for direct Internet
traffic; trust only verified upstream proxy egresses if an upstream exists.
Never broadly trust Internet/private ranges. Keep app/model/Qdrant ports private.
Check the installed Traefik version and SSE-compatible edge limits.

Keep challenges disabled until the independently built Next.js widget has a
real matching Turnstile public site key, server secret, permitted hostnames,
action `site_chat`, and fresh-token/retry handling. Only the public key belongs
in `NEXT_PUBLIC_TURNSTILE_SITE_KEY`; no server secret belongs in frontend code.
Required mode fails closed if key/token/configuration or verification is absent.
Review budgets, verify manifests, run through-proxy attack/stream checks, and
monitor resources before release. No live setting was activated by this task.

Record a known-good release/configuration before rollout. Roll back challenge
activation together with frontend token/site-key activation if needed; supplied
tokens still verify in optional mode. For application regression, restore the
known-good release and configuration via the established release procedure,
preserving the dirty live checkout. Restarting resets quota state. Do not relax
proxy trust/private ports as a rollback workaround.
