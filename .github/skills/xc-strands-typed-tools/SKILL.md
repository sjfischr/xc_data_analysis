---
name: xc-strands-typed-tools
description: 'Use when building or reviewing Strands agent tools for the XC platform: typed read-only tools, the approved analytics toolset, SQL guardrails (run_readonly_query, SQLite authorizer), or the analytics/resolution agent design.'
---

# Typed, read-only Strands tools

> Status: **illustrative pseudocode** — not yet validated against a pinned
> Strands SDK version (Task 3 records validated versions).
> Docs checked 2026-09-20: [Strands documentation](https://strandsagents.com/latest/documentation/docs/),
> [Python tools](https://strandsagents.com/latest/documentation/docs/user-guide/concepts/tools/python-tools/).

## The two agents

| Agent | Purpose | Memory |
|-------|---------|--------|
| Analytics agent | User-facing conversational analytics over the published SQLite snapshot | AgentCore short-term; long-term opt-in |
| Resolution agent | Stateless entity-resolution recommendations, one bounded JSON packet per case | none (no conversation memory) |

Both live in `src/xc_platform/agents` and must stay **runtime-neutral**: no
AgentCore, FastAPI, or CLI imports in the core package. Thin adapters bind
them to each runtime.

## Rules for every tool

1. Tools are plain Python functions with full type hints and docstrings —
   the docstring is the model-facing contract, so state units (integer
   milliseconds, integer meters) and limits explicitly.
2. Tools are **read-only**. They open the published snapshot with
   `mode=ro&immutable=1` and never mutate state.
3. Tools return structured JSON-safe data (dicts/lists of scalars), never
   DataFrames — the agents package must avoid pandas/compiled deps for
   AgentCore (Linux/ARM) portability.
4. Every tool call carries the correlation IDs (`request_id`,
   `agent_session_id`, `tool_call_id`) via context, not via model-visible
   arguments.
5. A tool must pin one `publication_id` for the whole request so multi-tool
   answers are internally consistent.

## The approved analytics toolset

`list_dimensions`, `find_athletes`, `find_schools`,
`get_athlete_progression`, `compare_athletes`, `get_team_scores`,
`simulate_team_scenario`, `get_saint_sebastian_standings`,
`get_improvement_candidates`, `run_readonly_query`, `build_chart_spec`.

Add tools only through spec change; do not let the model synthesize ad-hoc
capabilities.

## `run_readonly_query` guardrails (mandatory)

```python
# Illustrative — validate against the pinned SDK in Task 3.
from strands import tool

@tool
def run_readonly_query(sql: str, limit: int = 200) -> dict:
    """Run a single read-only SELECT (or WITH...SELECT) against the pinned
    publication. Rejects writes, PRAGMA, ATTACH, and multiple statements."""
    statement = parse_single_statement(sql)      # reject stacked statements
    conn = open_pinned_snapshot(mode="ro", immutable=True)
    conn.set_authorizer(deny_all_but_select)     # SQLite authorizer callback
    return execute_with_limits(conn, statement, row_limit=limit, time_limit_s=5)
```

- Enforce with the **SQLite authorizer**, not regex alone: deny INSERT,
  UPDATE, DELETE, DDL, PRAGMA, and ATTACH at the authorizer level.
- Apply row limits and wall-clock limits on every query.

## Anti-patterns

- A tool that accepts a file path, URL, or connection string from the model.
- Returning raw exception text (may leak paths/credentials) — return typed
  error codes and log details server-side via `xc_platform.security.redaction`.
- Tools that write to the database, S3, or disk.
