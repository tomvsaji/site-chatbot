---
title: "A tiny model, a busy VPS: simplifying my website assistant"
date: "2026-11-18"
draft: true
description: "What a CPU-only website assistant taught me about inference budgets and honest benchmarks."
---

<!-- Working draft: new VPS measurements and deployment verification are pending.
Do not publish performance claims until those results are added. -->

> Historical draft for the optional `CHAT_MODE=simple` design. The active default
> is the model-selected agent described in [current chatbot logic](../../CHATBOT_LOGIC.md).
> FAQ timings and one-CPU suggestions below do not describe its current runtime.

I wanted to put a small AI assistant on my personal website and write about
running AI with limited resources. The website lives on Netlify. The model runs
on my personal VPS, which I also use for experiments and tinkering.

The first practical problem was visible immediately: while the assistant was
generating an answer, both CPU cores could reach 100%.

That doesn't automatically mean the machine has failed. It does mean that an
optional website feature is competing with everything else I want to do on
that server. A chatbot that eventually answers one question isn't necessarily
a chatbot I want to expose to the internet.

## What I mean by local AI

This is self-hosted, CPU-only inference on a VPS. It isn't a model running in
a visitor's browser, and I haven't established that the server sits near every
visitor. Calling it an edge-AI deployment without that distinction would
oversell what I built.

The constraint is simpler: can a small model on a small server be useful enough
for a narrow website task?

## The original assistant had more work than the task needed

The stack uses a quantized LFM3-1.7B model through llama.cpp, MiniLM embeddings,
and hybrid keyword/vector retrieval with Qdrant. Published site content is the
assistant's factual source.

The original request path was an agent. The model could decide what to search,
read results, search again and then answer. Longer conversations could also
trigger a model-written summary and store searchable conversation embeddings.

Those features have uses. But for a short portfolio question, each extra model
pass competes for the same two cores. An earlier functional smoke test recorded
36.182 seconds for one factual answer and 19.397 seconds for a conversation
summary. Those are individual observations, not a benchmark distribution.

## A smaller default path

The proposed change gives the public assistant a simpler default:

1. Validate the request and apply quota and challenge policy.
2. Return an editorial answer for an exact common-question alias.
3. Otherwise, retrieve three relevant passages once.
4. Generate one short answer with citations.

There is no model-planned search loop, second generation to repair citations,
or model-written memory summary in this path. The model gets four recent
messages, bounded in length, to resolve short follow-ups. It no longer promises
to remember an entire long conversation.

The answer budget drops from 640 to 256 generated tokens, with a prompt asking
for at most 80 words. A token limit isn't a quality guarantee. Truncated answers
and unavailable citation IDs are rejected, rather than triggering another model
pass. Whether the shorter budget produces enough good answers still needs
real-model testing.

The previous agent remains available as an experimental mode. It doesn't need
to be the default for every visitor.

## Not every question needs inference

Specialization, contact details, side projects and writing topics are editorial
website content. For exact common questions, a short reviewed answer and a
source link can do the job without asking a model to rewrite them.

The interface labels those responses “Curated FAQ answer.” Generated answers
are labeled separately. This isn't a shared cache of conversations, and it
doesn't try to match arbitrary questions fuzzily to a canned reply.

The distinction matters for measurement too. Combining immediate FAQ replies
and slow generated replies into one average would hide the performance problem.

## Protecting a server I still want to use

One inference slot prevents concurrent generations. It doesn't stop that one
generation from using both cores. The proposed deployment adds a hard model CPU
quota of one logical CPU's worth of execution and sets embedding threads to one.

That is a tradeoff, not free performance. Generation may take longer. It also
doesn't reserve a core exclusively for my shell: the app, retrieval and other
workloads still share the machine.

The public request limits remain, with a lower default global hourly budget.
FAQs pass the same validation, quotas and optional challenge gate but don't
wait for inference. Other generated requests get a bounded busy response
instead of entering an unlimited queue.

There is still hardening work: complete challenge integration, verify proxy
and edge controls, improve quota fairness, and enforce total execution
cancellation. A CPU quota alone doesn't make an endpoint abuse-resistant.

## What I have actually measured

| Measurement | Evidence | What it establishes |
| --- | --- | --- |
| Earlier factual answer: 36.182 s | Historical VPS smoke | One old-agent sample |
| Earlier summary: 19.397 s | Historical VPS smoke | One old-agent sample |
| FAQ lookup + SQLite: median 6.74 ms, p95 12.21 ms | 200 real local operations in the editing workspace | The inference-free application path is inexpensive locally |
| New generated-answer latency and quality | Pending VPS run | No improvement claim yet |
| CPU headroom with the quota | Pending VPS run | No responsiveness claim yet |

The local FAQ measurement excludes HTTP, challenges, network, retrieval and
model execution. Comparing those milliseconds with the original agent's
seconds as a “speedup” would be misleading.

Behavioral tests establish the reduction in work: one retrieval and one
generation, without summaries or chat-history embeddings. HTTP tests verify
that FAQs work while inference is busy and still obey quota/challenge checks.

The next benchmark needs the real model, the same hardware, the same CPU quota
and comparable warm/cold conditions on both versions. I want actual answer
latency, rejected requests, CPU and memory use, and factual correctness.
A “Thinking…” event isn't the first answer token.

<!-- Add real VPS before/after measurements here. Review at least ten factual
questions plus follow-ups, unsupported questions and overlapping requests.
Report configuration, sample size and failures, not just the fastest answer. -->

## What would make it worth shipping?

The goal is a narrow assistant for occasional visitors. Curated answers can
handle common questions. Generated answers need to be short, source-backed
and quick enough that a visitor has a reason to wait.

My proposed targets are under one second end to end for FAQs, a median under
ten seconds for common generated answers, and a VPS that remains responsive.
Those are acceptance targets, not results established by this change. If the
model can't meet them under a sensible CPU cap, the next step is a narrower
demo, another inference setup or a hosted model.

The lesson so far is to budget the work per question. A small model doesn't
make an unnecessarily elaborate request path cheap. Deciding when not to
generate can matter as much as choosing the model.

## References

- [Chatbot source](https://github.com/tomvsaji/site-chatbot)
- [Benchmark method and local samples](../../benchmarks/README.md)
- [Docker resource constraints](https://docs.docker.com/engine/containers/resource_constraints/)
- [Turnstile server validation](https://developers.cloudflare.com/turnstile/get-started/server-side-validation/)

<!-- Before publishing: replace relative repository links for the blog, record
the deployed commit if released, and remove these editorial comments. -->
