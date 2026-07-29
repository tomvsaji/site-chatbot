# Chatbot evaluation set

The canonical machine-readable suite is `tests/eval_cases.json`. It contains
eight core scenarios plus one extended scenario. Scenarios preserve conversation
history so follow-up questions are evaluated against the chatbot's own preceding
answers.

## What it measures

- Accurate article and blog-title retrieval
- Coverage and explanatory depth
- Multi-turn topic resolution
- Non-repetition on clarification questions
- Correct separation of profile categories
- Citations to the expected published source
- Exact, source-free abstention for unsupported or adversarial requests
- End-to-end response latency

The suite deliberately includes the reported Composer 2 failure sequence:

1. `Tell me about the lessons for AI engineers blog.`
2. `What does this mean: Composer 2 is not just a product launch ...?`
3. `Why is it so efficient according to Tom?`
4. `But why does specialization itself make it more efficient?`

A successful model must explain the underlying ideas, cover several
source-supported reasons, and avoid repeating its preceding answer.

## Scenarios

| Scenario | Turns | Purpose |
|---|---:|---|
| `writing-catalog` | 2 | Writing topics and real article titles |
| `composer-explanation` | 4 | Deep explanation and repeated follow-ups |
| `sql-explorer-depth` | 3 | Detailed technical article comprehension |
| `dreamer-conversation` | 3 | Conversational coreference across an article |
| `profile-and-categories` | 3 | Profile facts and category boundaries |
| `reliable-sql-article` | 2 | Extended long-article coverage |
| `out-of-scope` | 3 | Missing and current information |
| `topic-switch` | 2 | Refusing an unrelated follow-up |
| `adversarial` | 3 | Prompt injection and unsafe requests |

There are 25 turns in total; 23 are in the `core` subset and two are tagged
`extended`.

## Scoring

Each supported turn declares:

- required source files;
- source-grounded concepts with acceptable wording variants;
- the minimum concept coverage needed to pass;
- minimum and maximum answer lengths;
- forbidden phrases where a known failure must be caught;
- an optional maximum similarity to the preceding answer.

Abstention turns pass only when the chatbot returns the configured fallback
verbatim with no sources. Supported turns must return citations matching the
source list produced by the API.

The runner reports strict pass rate, partial concept-coverage score, median
latency, p95 latency, and maximum latency. It can also write complete per-turn
results to JSON for model comparison.

## Running it

Run the core suite:

```bash
docker compose exec \
  -e EVAL_TAGS=core \
  -e EVAL_MODEL_LABEL=granite-4.0-1b-q8 \
  -e EVAL_OUTPUT_PATH=/tmp/granite-1b.json \
  app python evaluate.py
```

Run only the reported failure scenario while tuning:

```bash
docker compose exec \
  -e EVAL_SCENARIOS=composer-explanation \
  app python evaluate.py
```

Run all 25 turns:

```bash
docker compose exec app python evaluate.py
```

When comparing models, keep the corpus, chunking, retrieval thresholds, prompt,
context size, seed, and output-token limit fixed. Change only the model first.
That isolates the model effect; retrieval and prompt changes can then be tested
as separate experiments.
