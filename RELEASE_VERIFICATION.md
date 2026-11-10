# Release verification — native website chatbot API

## Scope and provenance

Branch: `feat/native-widget-security` in `tomvsaji/site-chatbot`.
Development checkout: `/home/hermes/projects/site-chatbot`.
The live checkout `/srv/site-chatbot` was not modified. Its existing source changes (including LFM3 migration work) were preserved in local baseline commit `b6f4452` rather than discarded. The hardening diff is measured against that baseline; the branch intentionally also carries those existing deployed-source changes relative to upstream main.

## Independently executed verification

- `python3 -m unittest discover -s tests -v`: **55 passed**.
- `python3 -m compileall -q app.py tests`: passed.
- `git diff --cached --check`: passed after keeping raw local test logs out of Git.
- Added-Python-line security scan: no matches for hardcoded credentials, shell injection patterns, eval/exec, or unsafe pickle loads. A pattern scan is not proof of security.
- Independent second review: approved; no confirmed security/logic findings. The reviewer independently ran all 55 tests and additional early-parser cleanup probes.
- Real loopback TCP transport smoke: SSE status/final/done with a labelled test model fixture returned 200, foreign Origin returned 403, invalid question type returned 400. These checks do not claim a real model answer from the new backend.
- Regression coverage includes untrusted XFF spoofing, trusted chain parsing, bounded client/global quotas, IPv6 identity grouping, malformed JSON/framing, read deadlines, blocked response writers, connection/model semaphore recovery, HTTP/0.9 rejection, browser-readable Retry-After, generic errors and mocked Turnstile server verification.

## Existing production API evidence, not a deployment of this branch

A controlled CLI POST to the existing deployed SSE endpoint returned HTTP 200 with status, claims, cited final answer and done in 38.86 seconds. A separate real browser request from `https://tomvsaji.com` returned HTTP 200 and the same complete event contract in approximately 60 seconds. The actual captured response was parsed successfully by the new frontend stream parser. Earlier busy responses were observed and not hidden. The small CPU-hosted model has limited capacity and can be slow.

## Required before production rollout

- Review and reconcile the preserved live-source baseline with upstream changes.
- An operator must verify Traefik forwarding policy and the app's actual proxy peer, then set narrow `TRUSTED_PROXY_CIDRS`. Empty default is fail-safe but groups visitors behind the proxy into one quota identity.
- Keep the app and model ports private; retain the single-inference capacity. Configure upstream resource/rate protections appropriate to the host.
- To require human verification, provision real Turnstile keys for the intended hostnames. Keep the secret server-side, build the frontend with the public site key, and coordinate `TURNSTILE_REQUIRED=true` only after both sides are ready.
- Turnstile is **not enabled by this branch push**. In required mode the old standalone chatbot UI is blocked because it does not send tokens; it is not exempted.
- Quotas are per-process/in-memory and reset at restart. These controls are not distributed DDoS protection or a guarantee of zero abuse.
- Docker/Traefik production deployment and live challenge verification were not performed. Follow the established reviewed deployment process only after explicit approval; preserve rollback release/configuration.

No production deployment, main-branch push, or real credential publication occurred during implementation/verification. Raw execution logs remain local under ignored `test-logs/`.
