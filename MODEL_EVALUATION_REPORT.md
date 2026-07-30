# I Tested Eight Small Open Models for a Two-Core RAG Chatbot. The Model Wasn’t the Main Problem.

Small local language models are attractive for a personal-site chatbot. They
keep data on the server, avoid per-token API costs, and are easy to place behind
an existing website. The hard part is that a model which fits comfortably on a
small VPS may still be too weak, too slow, or too easily distracted by imperfect
retrieval.

I ran a source-grounded, multi-turn evaluation on eight open-weight models and
quantizations that were roughly in the 2–2.5 GB storage class. The final result
was not simply “use a larger model.” Qwen3 4B was the strongest local generator,
but changing the retrieval and conversation design improved the pass rate far
more than changing the model.

## The setup

The deployment was intentionally constrained:

- 2 CPU cores
- 7.8 GiB host memory with no swap
- 4 GiB memory limit for the llama.cpp container
- 4,096-token model context
- one inference slot and two inference threads
- 320 generated tokens per request
- Qdrant plus `all-MiniLM-L6-v2` embeddings
- hybrid vector and BM25 retrieval
- Markdown-aware chunks capped at 140 lexical tokens

The benchmark used 23 core turns across writing discovery, technical article
questions, repeated follow-ups, profile facts, category boundaries, unsupported
questions, topic changes, and prompt injection. Answers had to cite the expected
source, cover source-grounded concepts, avoid repetition, stay within a useful
length range, and abstain exactly when the site did not contain the answer.

The most important scenario reproduced a real failure:

1. Ask about the “lessons for AI engineers” article.
2. Ask what a quoted conclusion means.
3. Ask why Composer 2 is efficient.
4. Ask why specialization itself improves efficiency.

This catches a behavior that isolated question-answer tests miss: a chatbot can
return a technically related sentence while failing to explain the user’s actual
follow-up.

## Model-only results

The table below uses the calibrated version 3 rubric. Calibration added valid
paraphrases such as “generates SQL” alongside “generate SQL” and accepted a
complete six-step answer at 83 words instead of requiring 100. No model answer
was regenerated during calibration; preserved outputs were replayed through the
same scorer.

| Model | Quantized file | Pass rate | Median | p95 | Approx. resident memory |
|---|---:|---:|---:|---:|---:|
| Granite 4.0 1B Q8_0 | 1.74 GB | 10/23 (43%) | 13.2s | 29.8s | 2.00 GiB |
| Granite 4.0 Micro 3B Q4_K_M | 2.10 GB | 9/23 (39%) | 27.3s | 52.3s | 3.48 GiB |
| Qwen3 4B Q4_K_M | 2.50 GB | 14/23 (61%) | 30.5s | 59.7s | 2.97 GiB |
| Phi-4 Mini Instruct Q4_K_M | about 2.50 GB | 8/23 (35%) | 42.1s | 118.8s | 3.80 GiB |

Granite 1B was fastest, but it produced the original terse and repetitive
answers. Granite Micro used much more memory and doubled latency without
improving the score. Phi-4 Mini was both slow and unstable under the 4 GiB
limit. Qwen3 4B gave the best explanations and abstention behavior, but the
model-only pass rate was still only 61%.

The model artifacts used were [Granite 4.0
Micro](https://huggingface.co/ibm-granite/granite-4.0-micro-GGUF),
[Qwen3 4B](https://huggingface.co/Qwen/Qwen3-4B-GGUF), and a GGUF conversion
of [Phi-4 Mini](https://huggingface.co/microsoft/Phi-4-mini-instruct).

## Additional same-size models

I screened four additional models on the nine most diagnostic turns. Candidates
that failed the first two catalog gates or had clearly unacceptable CPU latency
were not given the full 23-turn run.

| Model | File size | Screen result | Latency observation |
|---|---:|---:|---|
| Llama 3.2 3B Instruct Q4_K_M | 2.02 GB | 4/9 | 31.0s median |
| SmolLM3 3B Q4_K_M | 1.92 GB | 0/2 | 26.2s and 29.3s |
| Qwen3.5 4B IQ4_XS | 2.51 GB | 1/2 | 84.4s and 163.6s |
| Gemma 3 4B IT Q4_K_M | 2.49 GB | 1/2 | 32.1s and 146.2s |

[Llama 3.2 3B](https://huggingface.co/bartowski/Llama-3.2-3B-Instruct-GGUF)
was usable but weaker than Qwen3 on the critical subset.
[SmolLM3](https://huggingface.co/ggml-org/SmolLM3-3B-GGUF) failed both catalog
checks. [Gemma 3 4B](https://huggingface.co/ggml-org/gemma-3-4b-it-GGUF) and
Qwen3.5 were particularly poor fits for this two-core CPU environment because
of tail latency.

Qwen3 and Qwen3.5 also required reasoning to be disabled. With thinking enabled,
the model could consume the entire 320-token generation allowance before
producing the required JSON answer.

## What was actually wrong with the RAG flow

The first benchmark made four retrieval defects visible.

### Section headings were treated as article titles

When asked for blog posts, the original system retrieved headings such as “Why
Dreamer Matters” and “Lessons for AI engineers.” Larger models sometimes made
this worse by listing external reference links from the bottom of an article.

The fix was to route writing-catalog questions directly to the canonical writing
index and to exclude Markdown reference-detail blocks from embedding.

### Follow-ups had no stable article state

The old system appended only the preceding user question to a new search. A
generic follow-up such as “Walk me through the process” could therefore drift
from the SQL Explorer article into Dreamer.

The corrected flow resolves explicit article names to a source file and carries
that source lock across follow-ups. Retrieval stays within the selected article
until the user changes topics.

### Two chunks per source were insufficient

The retriever limited every source to two chunks. That is useful for broad
profile questions, but it prevents a six-step article explanation from seeing
all six sections.

The corrected flow allows multiple chunks from a locked article and selects one
representative chunk from each numbered section for step-by-step questions.

### Showing the model its previous answer encouraged copying

The original generation prompt included the immediately preceding assistant
answer. Qwen often copied it verbatim when asked “why?” again.

History is now used to resolve the topic and retrieve the right source, but
prior assistant prose is not sent back to the generator. This preserves
conversation state without turning the previous answer into a template.

## The corrected result

With the same Qwen3 4B Q4_K_M model and the corrected RAG flow:

| Configuration | Pass rate | Median | p95 | Maximum |
|---|---:|---:|---:|---:|
| Qwen3 4B, original RAG | 14/23 (61%) | 30.5s | 59.7s | 76.4s |
| Qwen3 4B, corrected RAG | 23/23 (100%) | 32.1s | 81.9s | 82.7s |

The raw pre-calibration score for the final run was 18/23. Manual inspection
showed that four of the five failures were complete, grounded answers rejected
for morphology or unnecessarily high word-count thresholds. The original raw
reports and calibrated replay reports are both retained under `eval-results/`.

The quality improvement came at almost no median-latency cost, although tail
latency remains high. Streaming makes the interface feel responsive, but it
does not change the underlying CPU time.

## Recommendation

For this exact VPS, Qwen3 4B Q4_K_M with reasoning disabled is the best local
choice tested. It fits under the 4 GiB model-container limit, follows the
structured citation schema, and gives materially better explanations than the
other candidates.

I would not describe it as fast. A 32-second median and an 82-second p95 are
acceptable for a low-traffic personal demonstration with streaming, but not for
a broadly used production chatbot. For a sub-10-second target, the next step is
hosted inference or a server with substantially more CPU throughput—not another
similarly sized model on the same two cores.

The more general lesson is that model selection should come after a retrieval
evaluation exists. On this task, changing from Granite 1B to Qwen3 improved the
score by 18 percentage points. Keeping Qwen3 and fixing the RAG flow improved it
by another 39 points. The retrieval system, not the leaderboard, determined
whether the chatbot actually answered the question.
