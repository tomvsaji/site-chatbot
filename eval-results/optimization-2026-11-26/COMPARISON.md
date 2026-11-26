# Observed chatbot optimization comparison

One sequential six-question conversation per variant on same actual host and isolated pinned llama.cpp runtime with 256MiB prompt cache. Disposable conversation memory; read-only existing Qdrant index. Fixed priming request, cache not flushed between variants. Baseline failures change subsequent conversation history. Not an accuracy score or statistical latency distribution. Agent elapsed excludes browser/network admission.

| Case | Baseline (s) | Candidate (s) | Completion |
|---|---:|---:|---|
| specialization | 48.49 | 22.37 | ok → ok |
| clients | 75.58 | 20.55 | ok → ok |
| sql_experience | 32.72 | 29.40 | error → ok |
| article_summary | 10.07 | 19.28 | ok → ok |
| conversation_summary | 34.31 | 31.84 | ok → ok |
| first_question | 44.65 | 19.53 | ok → ok |

Total: 245.827 → 142.966s (41.8% lower observed wall time).

Completion means the agent returned a response, not that all claims were factually correct. Manual content review is recorded separately.
