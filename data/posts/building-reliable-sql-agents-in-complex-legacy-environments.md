# Building Reliable SQL Agents in Complex On-Prem SQL Environments

Source: https://tomvsaji.com/blogs/building-reliable-sql-agents-in-complex-legacy-environments

Published: April 4, 2026

Reading time: 11 min read

Lessons from building and hardening an enterprise text-to-SQL agent in a complex on-prem SQL Server environment with linked-server dependencies.

Some projects teach you more than others. This was one of them.

Over the past year, our team built an AI agent capable of querying a complex, on-premises SQL environment for a large enterprise client. What started as a focused, scoped project gradually grew in complexity and ambition — and by the end, we were deep in the architecture, rebuilding core components and shipping the system to UAT. Here's what we learned.

## The Environment We Were Working With

Before talking about the agent, it's worth understanding the data environment — because that context shapes every decision we made.

The database was a complex on-premises SQL Server environment with linked server dependencies across multiple business domains. Behind-the-scenes coupling made it fragile under complex joins and cross-domain queries.

There was no fuzzy search to handle misspelled names, and no text indexing to accelerate partial match queries on large columns.

This wasn't a full data warehouse implementation with a unified semantic layer. It was a distributed operational reporting stack that required careful query orchestration.

On top of that, every query the agent generated had to respect role-based access rules across a strict organizational hierarchy, each level with a different data scope. Permission filtering on dynamic, AI-generated SQL is not a solved problem. Getting it wrong in an enterprise environment has real consequences.

This wasn't a clean sandbox. It was the kind of environment most production AI systems actually live in.

## What the Foundation Got Right

Several early decisions held up well over time and didn't need revisiting:

- **Permission enforcement in code, not prompts.** Role-based access was never left to the model to enforce. A separate authorization layer resolved each user's data scope before any query ran — pulling dynamically from the database, with a config file as fallback. This was the right call from day one.
- **Per-data-source tools.** Each business domain had its own plugin responsible for generating and executing queries. For isolated use cases, this worked cleanly and kept concerns separated.
- **Partial data caching.** Only a fixed page of rows was served to the agent at a time, with the rest stored server-side. This kept latency low, reduced token usage, and made the system feel fast to users.
- **Targeted SQL Server Full-Text Search indexing.** We added Full-Text Search indexes on selected high-cardinality text columns and shifted relevant lookups to `CONTAINS`-style queries. This improved token-based keyword lookups at scale; it did not turn leading-wildcard `LIKE '%x%'` patterns into cheap operations.
- **User-specific export caches.** A lightweight system let users retrieve historical query results as Excel files or trigger fresh queries on demand — a small UX detail that made a meaningful difference in daily use.
- **Comprehensive logging.** Every tool call, query, and error was tracked using OpenTelemetry from the start — creating a foundation for debugging and building trust with stakeholders.

## Where Things Got Complicated

As the system matured and user expectations grew, a few structural limitations became clear.

The prompt design was overly rigid — the agent struggled with queries that crossed multiple data sources or didn't match the patterns it had been designed to expect. Five separate domain-specific plugins meant the agent had to know in advance which domain a question belonged to, which broke down for anything cross-domain. The permission filtering, while present, was implemented as basic `WHERE` clause injection — fragile the moment a query has its own CTEs, subqueries, or aggregates.

To be clear: linked servers themselves weren't the core structural problem. The bigger issue was the absence of a semantic layer, which forced the agent to navigate raw joins and domain mappings blindly.

A useful turning point came when the client engineering team began converting unstable views into materialized views for critical paths. That infrastructure work, combined with agent-side guardrails, materially improved reliability.

These weren't failures of effort. They were the natural result of building in an environment with limited visibility and a lot of unknowns. But they created a ceiling the system kept hitting.

## The Shift: A Unified Explorer Agent

The most significant improvement came from consolidating the five domain plugins into one unified agent — built on Semantic Kernel with Azure OpenAI — using what we call the explorer-first pattern.

Instead of routing questions to pre-built domain generators, the agent now discovers what it needs before writing SQL. It exposes a small, focused toolset to the orchestrator:

- **List Tables** — returns a catalogue of all available data sources with descriptions and join keys. Also injects role-scoped context for the current user so the agent understands what data is in scope.
- **Get Schema** — returns column definitions and sample rows for a specific source.
- **Get Distinct Values** — returns unique values for a column with optional search. We then apply similarity matching in the application layer to resolve misspellings and near-matches safely.
- **Resolve Name to Code** — translates a human-readable entity name into its internal identifier code, preventing the agent from ever filtering on name columns directly.
- **Execute SQL** — validates, secures, and runs the query with full retry logic.

The agent calls these in sequence: discover → inspect schema → resolve filter values → generate SQL → validate and secure → execute. Upgrading to GPT-5.2 made this pattern significantly more reliable — stronger instruction-following and contextual reasoning meant fewer hallucinated column names and better multi-source join handling.

## Smarter Permission Control with Security CTEs

One of the most important architectural changes was how we rethought permission enforcement at the query level.

The old approach injected `WHERE` clauses directly into model-generated SQL. This is fragile — it breaks the moment the query has its own CTEs, uses self-joins, or has a complex aggregation structure.

The new approach uses what we call the **Security CTE Prefix** pattern. Instead of modifying the model's SQL, we prepend pre-filtered security CTEs that replace raw data source references entirely. The model's query is treated as a black box:

```sql
-- Model generates:
SELECT department, COUNT(*) AS headcount
FROM employees
GROUP BY department

-- System transforms to:
WITH _sec_employees AS (
  SELECT * FROM employees
  WHERE department IN ('dept_a', 'dept_b')
)
SELECT department, COUNT(*) AS headcount
FROM _sec_employees
GROUP BY department
```

Every data source reference in the model's SQL — however it's written — gets replaced with the pre-filtered CTE. CTEs, subqueries, self-joins, and aggregates all work safely without special cases. There's also a post-execution safety net: after results return, any row outside the user's authorized scope is stripped before the data reaches the model.

## A Second Review Layer for Complex Queries

For queries that cross multiple data sources, use CTEs, or contain nested selects, we added an optional LLM-based SQL review step. Before execution, the generated SQL is passed to a second model call with a structured prompt asking it to return a simple decision: approve, revise, or block.

This reviewer doesn't need to catch everything — the deterministic validator and security CTE filter are the real safety net. The reviewer's job is correctness: catching duplicate inflation from missing `DISTINCT`, wrong source usage, or broken aggregations. It runs with low reasoning effort and a tight timeout so it doesn't slow the user experience.

## Observability

A reliable agent isn't just one that produces good outputs — it's one you can inspect and verify.

Every database call is wrapped in an OpenTelemetry span with the query, row count, and error state attached. Semantic Kernel's own traces flow into the same pipeline. This gave us end-to-end visibility from the user's question through to the final SQL execution — which proved critical during high-stakes UAT sessions with external stakeholders.

## What I'd Do Differently from Day One

If I were starting fresh:

- **Start with data modernization.** Unified schema design, quality constraints, and proper join performance via a data lake rather than fighting a fragmented linked-view structure throughout development.
- **Add a semantic model early.** A semantic model abstracts raw schema into named business entities, documented relationships, and reusable measures. In a mature warehouse this comes naturally, but even a versioned metadata contract is enough to give the agent stable semantics instead of rediscovering structure at query time.
- **Define agent-friendly metadata upfront.** Column descriptions, join keys, and domain rules should live in source control from the start — not discovered iteratively through production failures.
- **Use the explorer pattern from day one.** Domain-specific plugins feel clean early and become a maintenance burden fast. A single agent that discovers schema dynamically scales much better.
- **Enforce access control natively at the infrastructure level.** Application-layer CTE injection is a solid fallback for environments you don't fully control, but native row-level security is a stronger long-term foundation.
- **Add query-result caching early.** Repeated queries on stable data are an easy performance win that meaningfully improves the user experience.

## A Note on Where AI Agents Are Heading

There's a lot of excitement right now about fully autonomous agents — systems that can plan, act, and iterate with minimal human intervention. That's a real and important direction.

But in regulated industries and enterprise environments, the properties that matter most are different: auditability, permission enforcement, predictable behavior, and the ability to explain exactly how an answer was reached. The patterns here — structured tooling, CTE-based access control, deterministic validation, full execution tracing — aren't legacy thinking. They're what production AI requires when the stakes are real and the data is sensitive.

The agent frameworks will keep evolving. The fundamentals won't.

## The Open-Source Version

The core patterns from this project — the explorer-first toolset, the Security CTE Prefix, and the read-only validator — are available as an open-source reference implementation at [github.com/tomvsaji/text-to-sql-agent](https://github.com/tomvsaji/text-to-sql-agent). That version uses LangGraph and a SQLite demo database rather than Semantic Kernel and SQL Server, but the security architecture and agent design transfer directly.
