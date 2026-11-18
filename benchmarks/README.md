# Benchmark evidence and rollout gate

## Actual measurement

On 2026-11-18, 200/200 local FAQ lookups plus private SQLite writes completed:
median **6.74 ms**, nearest-rank p95 **12.21 ms**. Raw samples are in
[local-faq-2026-11-18.json](local-faq-2026-11-18.json).

This is an in-process measurement in the editing workspace. It excludes HTTP,
quotas, challenges, network, retrieval and model execution. It is not a VPS
inference benchmark and isn't comparable to historical model latency.

```bash
python3 benchmark.py --faq-only-local --label simple-local \
  --output benchmarks/local-faq.json
```

Behavioral tests verify one retrieval/one generation, no chat embeddings or
compaction, irrelevant-query abstention, and FAQ availability during inference.
HTTP tests verify FAQ quota/challenge enforcement.

Historical VPS smoke: 36.182 seconds for one factual answer and 19.397 seconds
for one summary. These are old individual samples, not a before/after benchmark.
See upstream eval-results/integration-real-model.jsonl. **New VPS generation
latency, CPU headroom and factual quality remain unmeasured.** This session
cannot access Docker or the VPS checkout and has no configured SSH connection.

## Real endpoint run

Use a controlled preview with the real model. Development Compose shares
production inference: schedule the run to avoid interrupting other work. Its
app state and Qdrant are isolated. Never disable public production challenges
or bypass its quotas for a benchmark.

```bash
python3 benchmark.py --url http://127.0.0.1:8081 \
  --label '<source SHA>; simple; actual CPU quota' \
  --overlap --output benchmarks/simple-vps.json
```

On the VPS, add `--container <exact-model-container-name>` for Docker CPU/memory
sampling. Discover the name with docker ps. Sampling failure is recorded, not
interpreted as zero CPU. Docker 100% is approximately one logical CPU. Monitor
host CPU, swap, SSH and other workloads separately. The production Compose CPU
cap doesn't change an already-running shared model used by development.

The runner makes five sequential requests, waits 22 seconds between them,
then optionally waits 61 seconds before submitting three generated requests
together. It preserves cookies for follow-ups and records actual answers,
sources, HTTP rejections and Retry-After. There are no retries or quota bypasses.
A challenge-required endpoint rejects this token-free runner; use a private
preview with its own policy. This doesn't verify production challenges.

FAQ/generated/out-of-scope/rejected results are summarized separately. First
status isn't first answer text. Text is buffered until validation, so first
answer text is near final-answer time. Review answers and supporting passages
manually; citation existence doesn't prove correctness.

Compare old/new releases with the same hardware, model, CPU quota, questions,
and warm/cold state. Repeat runs need deliberate isolated benchmark quotas.
Don't restart production to reset limits. Default samples are a smoke test,
not enough for a reliable production p95.

## Proposed usability gates — not achieved measurements

- Curated FAQs: under 1 second end to end.
- Common generated answers: median under 10 seconds and p95 under 20 seconds
  in a larger controlled run. If missed, narrow the demo or simplify further.
- At least 9/10 manually reviewed factual answers correctly use supporting
  evidence; also check follow-ups, unsupported queries and prompt injection.
- One overlapping generated request succeeds; excess requests get bounded
  busy responses; a FAQ still succeeds while generation is active.
- Normal SSH/tinkering stays responsive, with no OOM/restarts or sustained swap.
  Verify CPU quota in the running container rather than assuming YAML is active.

Don't deploy solely on mocked tests or local FAQ timings. The Netlify frontend
must show answer mode, retain correct session handling and avoid retry loops.
Turnstile/edge protection remains necessary before a broadly promoted launch.
