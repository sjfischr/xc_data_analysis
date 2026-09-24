---
name: kiro-spec-workflow
description: 'Use when working on the XC Data Platform Modernization spec: executing, resuming, closing off, or reviewing tasks from .kiro/specs/xc-data-platform/tasks.md, or when the user says "next task", "continue the spec", "close the task", or references Kiro spec tasks. Defines how to execute tasks Kiro-style: read requirements/design context, implement, validate, then check off the task in tasks.md.'
---

# Kiro Spec Execution Workflow (xc-data-platform)

This repository is driven by a Kiro-style spec. Execute its tasks exactly as Kiro would.

## Spec files

| File | Purpose |
|------|---------|
| [.kiro/specs/xc-data-platform/requirements.md](../../../.kiro/specs/xc-data-platform/requirements.md) | 20 numbered requirements with acceptance criteria (`R<req>.<criterion>` notation) |
| [.kiro/specs/xc-data-platform/design.md](../../../.kiro/specs/xc-data-platform/design.md) | Architecture and design decisions |
| [.kiro/specs/xc-data-platform/tasks.md](../../../.kiro/specs/xc-data-platform/tasks.md) | Ordered implementation checklist — the single source of truth for progress |
| `.kiro/specs/xc-data-platform/tasks.meta.json` | Kiro's execution history. Do NOT hand-edit; it is informational only |

## Per-task execution loop

1. **Select** the first unchecked (`[ ]`) task in `tasks.md`, honoring the dependency order (Task 1 → 2 → 3 → ... ; 8 and 9 may run in parallel). Sub-tasks complete before their parent is checked.
2. **Read context**: the `_Requirements: ..._` references in the task, plus relevant design.md sections. Survey existing code first — earlier (possibly hung) sessions may have partially completed work.
3. **Implement** only what the task's bullets specify. Preserve legacy scripts, the Streamlit dashboard, Heroku deployment, and historical artifacts until the parity/cutover gates pass.
4. **Validate**: run the tests/checks the task specifies (and the existing test suite where relevant). A task is complete only when implementation, validation, documentation, and demonstration outcomes are all done.
5. **Close off**: change the task's `- [ ]` to `- [x]` in `tasks.md`. Check a parent task only when all of its sub-tasks are checked.
6. **Report** briefly what was done and what task is next, then stop or continue as the user directs.

## Hard rules (from the spec's execution rules)

- Never commit, print, or read credential values. Redact secrets in all output. `tavily_api_key` at repo root must never be read or echoed.
- Ordinary tests/CI must never invoke Tavily, Bedrock, AgentCore, or live RunSignup endpoints. Live probes are opt-in only (Task 3 feasibility harness).
- Every data-changing task preserves source provenance and provides a rollback path.
- All dependencies are pinned exactly (`==`, SHA-pinned actions). Install from lockfiles (`requirements.lock` with hashes, `web/pnpm-lock.yaml` with pnpm). See [docs/dependency-review.md](../../../docs/dependency-review.md).
- Pre-commit (gitleaks + pin checks) must stay green: `pre-commit run --all-files`.
- **Cost is not a requirement or a gate** (owner decision 2026-09-20). Do not
  reintroduce budgets, spend forecasts, or cost thresholds. Only runaway-loop
  caps remain, and they are reliability controls.
- **2023-2025 are frozen seasons.** Migrate them exactly as the baseline holds
  them; never correct or backfill them from the live source. Live intake targets
  **2026 only**; an import resolving to a frozen season is refused.

## Owner gates — STOP and ask the user

Do not proceed past these without explicit user approval recorded in the conversation:

1. **Task 3.8** — feasibility go/no-go. *(Amended 2026-09-20: the cost-forecast condition was removed; cost is no longer a gate.)*
2. **Task 5.5** — acceptance of the migrated historical baseline.
3. **Task 17.4** — production acceptance.
4. **Task 18.3** — Heroku decommission confirmation.

Also user-dependent: any live AWS/Tavily/RunSignup probe in Task 3 (owner
opt-in granted 2026-09-20 for initial probes). Task 2's guidance packages were
converted from Kiro Powers to repository-local skills under `.github/skills/`
by owner decision on 2026-09-20; they activate automatically.

## Validation commands

```powershell
python scripts/check_dependency_pins.py     # pin/lockfile integrity
python -m pytest src -q                     # Python unit tests
pre-commit run --all-files                  # secret scan + hygiene hooks
```
