---
name: xc-strands-local-testing
description: 'Use when testing XC Strands agents or tools: fixture snapshots, mocked model providers, agent-contract tests, and the no-live-calls-in-CI rules.'
---

# Local and CI testing for Strands agents

> Status: **illustrative pseudocode** — not yet validated against a pinned
> Strands SDK version (Task 3 records validated versions).
> Docs checked 2026-09-20: [Strands documentation](https://strandsagents.com/latest/documentation/docs/).

## The testing pyramid for this project

1. **Tool unit tests (most coverage).** Tools are plain typed functions —
   test them directly against a fixture SQLite snapshot built from
   `tests/fixtures/baseline/` numbers. No agent loop, no model.
2. **Agent-contract tests.** Drive the agent loop with a **mocked model
   provider** that replays recorded tool-use decisions; assert tool-call
   sequences, budget enforcement, and typed error paths.
3. **Live evals (opt-in only).** Real-model evaluation runs are explicitly
   opt-in (marked `live` in pytest), never part of ordinary CI, and use the
   feasibility harness's budget/redaction rules.

## Hard rules (from the spec execution rules)

- Ordinary tests and CI must **never** invoke Bedrock, AgentCore, Tavily, or
  live RunSignup endpoints.
- Tests use dummy credential-shaped values only; assert redaction with the
  helpers in `src/xc_platform/security`.
- The read-only guarantee is a test target: every analytics tool test asserts
  that write statements are rejected (SQLite authorizer denies
  INSERT/UPDATE/DELETE/DDL/PRAGMA/ATTACH).

## Pattern: fixture snapshot per test session

```python
# Illustrative — validate against the pinned SDK in Task 3.
@pytest.fixture(scope="session")
def fixture_snapshot(tmp_path_factory) -> Path:
    """Build a small SQLite snapshot with known baseline numbers."""
    db = tmp_path_factory.mktemp("snap") / "xc.db"
    build_test_snapshot(db, seasons=[2023], teams=3, athletes=25)
    return db

def test_team_scores_match_baseline(fixture_snapshot: Path) -> None:
    scores = get_team_scores(season=2023, meet=1, division="JV", gender="Boys")
    assert scores == EXPECTED_FROM_BASELINE  # tests/fixtures/baseline/
```

## Pattern: mocked model provider

```python
# Illustrative — validate against the pinned SDK in Task 3.
agent = Agent(model=ReplayModel("recorded/compare_athletes_happy_path.json"),
              tools=ANALYTICS_TOOLS)
result = agent("Compare athlete A and athlete B in 2024")
assert result.tool_calls == ["find_athletes", "compare_athletes"]
```

## Anti-patterns

- A test that "just quickly" hits a real model or real API endpoint.
- Asserting on free-form model prose instead of tool calls and typed outputs.
- Fixtures containing real youth PII beyond the approved baseline fixtures.
