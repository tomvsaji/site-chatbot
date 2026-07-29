# How a SQL Agent Thinks: Walking Through the Explorer Pattern

Source: https://tomvsaji.com/blogs/how-a-sql-agent-thinks-explorer-pattern

Published: April 4, 2026

Reading time: 11 min read

Most text-to-SQL demos show the happy path. This post walks through the real explorer-first workflow an SQL agent uses to disambiguate schema, resolve filters, and enforce security before execution.

Most text-to-SQL demos show you the happy path: clean question in, correct SQL out. Real production environments are messier. Table names are ambiguous. Columns store codes instead of labels. A plausible query can return a confidently wrong answer with no runtime error.

In my [previous post](https://tomvsaji.com/blogs/building-reliable-sql-agents-in-complex-legacy-environments), I covered a year of building a SQL agent on top of a complex on-premises SQL Server environment with linked-server dependencies across business domains — what worked, what hurt, and how the architecture evolved. That post covered the **why**. This one covers the **how**.

Specifically: what does the agent do between receiving a question and returning an answer?

The answer is an **explorer-first pattern**. Instead of generating SQL immediately, the agent explores the data landscape first, resolves ambiguity, and only then writes executable SQL.

## The Problem With Generating SQL Directly

The naive pipeline looks like this:

`user question → LLM → SQL → execute → answer`

It works on demo datasets. It fails in production for predictable reasons:

1. **Wrong column assumptions**
   - Model writes `WHERE department = 'Operations'`.
   - Actual column stores department codes (for example `D042`).
   - Query returns zero rows.
   - No error is raised.

2. **Wrong table selection**
   - User asks about absences.
   - There are separate tables for approved leave vs unexplained no-shows.
   - Model selects the wrong source and returns a plausible but wrong answer.

3. **Wrong join logic**
   - Model joins on a key that *looks* right but is not the true relational key.
   - Results are inflated or missing.
   - Again, no error.

The explorer pattern addresses all three by forcing the agent to do grounded discovery before generation.

## The Explorer Pattern: Step-by-Step

Let’s use a realistic question:

> “How many employees in the operations team were absent last month?”

This question contains at least three ambiguities:

- What does “operations team” map to in the data model?
- What definition of “absent” should apply?
- Which source contains the canonical absence signal?

### Step 1 — Discover available data sources

The agent starts with `list_tables`. Always.

Example result:

- `employees` — staff profiles, department codes, job info
  - join key: `employee_id`
- `attendance` — daily attendance records
  - join key: `employee_id`, `date`
- `approved_leave` — approved leave requests
  - join key: `employee_id`
- `absence_records` — unexplained absences and no-shows
  - join key: `employee_id`

From this, the agent identifies likely candidates:

- `employees` for department scoping
- `absence_records` for unexplained absence tracking

This is also where role-scoped context is injected. If the user is a department manager, security context can constrain possible departments before any SQL is generated.

### Step 2 — Inspect schema and sample data

Next, the agent calls `get_schema` for candidate sources.

For `employees`, it might see:

<table>
  <thead>
    <tr>
      <th>column</th>
      <th>type</th>
      <th>sample</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>employee_id</td>
      <td>VARCHAR</td>
      <td>EMP001</td>
    </tr>
    <tr>
      <td>department</td>
      <td>VARCHAR</td>
      <td>D042</td>
    </tr>
    <tr>
      <td>dept_name</td>
      <td>VARCHAR</td>
      <td>Operations</td>
    </tr>
    <tr>
      <td>status</td>
      <td>VARCHAR</td>
      <td>Active</td>
    </tr>
  </tbody>
</table>

For `absence_records`:

<table>
  <thead>
    <tr>
      <th>column</th>
      <th>type</th>
      <th>sample</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>employee_id</td>
      <td>VARCHAR</td>
      <td>EMP001</td>
    </tr>
    <tr>
      <td>absence_date</td>
      <td>DATE</td>
      <td>2025-11-03</td>
    </tr>
    <tr>
      <td>reason_code</td>
      <td>VARCHAR</td>
      <td>NP</td>
    </tr>
    <tr>
      <td>approved</td>
      <td>BIT</td>
      <td>0</td>
    </tr>
  </tbody>
</table>

Now the agent is grounded in facts:

- `department` stores codes, not labels.
- Absence semantics may depend on `reason_code` or `approved`.

### Step 3 — Resolve user filter values

The agent cannot safely assume `department = 'Operations'`.

So it resolves values explicitly:

```text
get_distinct_values(table="employees", column="dept_name", search="operations")
→ ["Operations and Logistics", "IT Operations", "Field Operations"]
```

Three matches means ambiguity. The agent asks a clarification question instead of guessing:

> “I found three departments matching ‘operations’: Operations and Logistics, IT Operations, and Field Operations. Which one did you mean?”

After clarification, the agent can map to the exact internal code (for example `D042`) and continue.

This one step prevents a huge class of silent wrong answers.

### Step 4 — Generate SQL only after grounding

With source, schema, and filter values confirmed, SQL generation is straightforward:

```sql
SELECT COUNT(DISTINCT a.employee_id) AS absent_count
FROM absence_records a
JOIN employees e ON a.employee_id = e.employee_id
WHERE e.department = 'D042'
  AND a.absence_date >= '2025-11-01'
  AND a.absence_date <  '2025-12-01';
```

Notice `COUNT(DISTINCT a.employee_id)`, not `COUNT(*)`. If one employee has multiple absence records in the month, `COUNT(*)` would overstate the answer.

### Step 5 — Apply the security wrapper

Before execution, a deterministic security layer rewrites table access.

Instead of injecting ad-hoc `WHERE` clauses into arbitrary model SQL (which is brittle for nested CTEs and subqueries), the system prepends pre-filtered security CTEs and replaces base table references.

```sql
-- Model-generated SQL:
SELECT COUNT(DISTINCT a.employee_id) AS absent_count
FROM absence_records a
JOIN employees e ON a.employee_id = e.employee_id
WHERE e.department = 'D042'
  AND a.absence_date >= '2025-11-01'
  AND a.absence_date <  '2025-12-01';

-- Executed SQL after security rewriting:
WITH _sec_employees AS (
  SELECT * FROM employees
  WHERE department IN ('D042')
),
_sec_absence AS (
  SELECT a.*
  FROM absence_records a
  JOIN _sec_employees e ON a.employee_id = e.employee_id
)
SELECT COUNT(DISTINCT a.employee_id) AS absent_count
FROM _sec_absence a
JOIN _sec_employees e ON a.employee_id = e.employee_id
WHERE e.department = 'D042'
  AND a.absence_date >= '2025-11-01'
  AND a.absence_date <  '2025-12-01';
```

This pattern is robust across CTEs, self-joins, and complex aggregations.

A deterministic validator also blocks non-read statements by stripping comments and only allowing top-level `SELECT` or `WITH` execution. `DROP`, `DELETE`, `INSERT`, and procedure execution are rejected before reaching the database.

Finally, post-execution filtering strips any out-of-scope rows before results are exposed to the model. That creates three independent enforcement layers.

### Step 6 — Handle failure modes explicitly

The happy path is only half the story.

#### Failure mode A: no match from `get_distinct_values`

```text
get_distinct_values(table="employees", column="dept_name", search="ops")
→ []
```

No verified match means no query generation. The agent asks for correction:

> “I couldn’t find a department matching ‘ops’. Could you share the full department name?”

#### Failure mode B: first execution error

```text
execute_sql(query) → Error: invalid column name 'absence_date'
```

The agent reopens schema, finds the right field (for example `record_date`), rewrites SQL, and retries.

This retry loop is bounded: enough to recover common schema mismatches, but not enough to spin indefinitely.

## Optional Reviewer Step for Complex SQL

For multi-source or nested queries, a second model pass can act as a quality reviewer.

Input: generated SQL.  
Output: strict JSON decision.

```json
{
  "decision": "revise",
  "reason": "COUNT(*) inflates totals because an employee can have multiple daily records",
  "revised_sql": "..."
}
```

Typical decisions:

- `approve`
- `revise`
- `block`

This reviewer is a correctness gate, not the primary safety layer. If it fails or times out, deterministic validation and security rewriting still control execution.

## What You Get From Explorer-First

Compared with direct generate-and-run, this pattern gives you:

- **Disambiguation before execution**
  - Ambiguous names are resolved to concrete values before SQL is written.
- **Schema-grounded generation**
  - Queries are based on actual columns and types, not inferred guesses.
- **Traceable reasoning**
  - You can log each step: source discovery, schema checks, value resolution, SQL revisions.
- **Security invariant across SQL complexity**
  - Permission enforcement works even with nested, generated, or complex query structures.

Yes, this adds latency. In enterprise analytics, that is usually the right trade: a slightly slower correct answer is better than a fast confident error.

## Code Reference

I published these patterns as an open-source reference:

- **Repo:** `github.com/tomvsaji/text-to-sql-agent`
- **Explorer workflow:** `SQL_dynamic/graph_agent.py`
- **Security CTE prefix + validator:** `shared.py`

The reference implementation uses LangGraph + SQLite for portability, while production used Semantic Kernel + SQL Server. The core pattern transfers cleanly across stacks.

If you’re building something similar, I’d love to compare notes. I post occasionally at **tomvsaji.com** and on LinkedIn.
