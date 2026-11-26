# Optimization experiment artifacts

These are synthetic evaluation conversations about public website content, not real visitor chats. **Generated answers include known errors and are not authoritative website facts.** Read CONTENT_REVIEW.json alongside timings.

Both November 26 runs used the same isolated llama.cpp b11960 runtime, existing Q4_K_M weights, 2 CPU threads, 8K context and a 256 MiB RAM prompt-cache budget on the actual host. The candidate used MAX_AGENT_SEARCHES=1; baseline used its existing default of 3. Memory was disposable and the existing vector index was queried read-only, without its destructive rebuild constructor.

The benchmark calls agent.run_agent directly; it excludes browser/network admission. Cookie/CORS/widget behavior is checked separately after deployment. A priming request does not flush all caches. This is an observational sequence, not a statistical SLA or general accuracy score.

Run `python compare.py baseline candidate` in this directory to reproduce the archived comparison without contacting services. Live reruns require deliberately prepared isolated Docker containers: `python run-host.py LABEL CANDIDATE_CONTAINER ISOLATED_LLM_CONTAINER`; this executes real inference. `planner-preflight.py` uses the labelled historical context fixture, not visitor messages. Container names/defaults describe the original host; verify them before live runs. Model weights and credentials are excluded.
