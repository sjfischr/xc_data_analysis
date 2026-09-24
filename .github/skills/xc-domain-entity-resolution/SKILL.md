---
name: xc-domain-entity-resolution
description: 'Use when resolving athlete/school identities: the resolution ladder, hard-conflict never-merge rules, the resolution agent JSON contract, and provenance/reversibility requirements.'
---

# Entity-resolution policy

> Status: **normative policy** (source: spec design.md entity-resolution
> section; Requirements R6–R7).

## Resolution ladder (apply in order, stop at first match)

1. **Source-ID link** — the source system's stable ID already maps to a
   canonical entity.
2. **Approved alias** — a human-approved alias for that source+context.
3. **Exact normalized key** with no conflicting candidate.
4. **Deterministic scoring** — rule-based candidate scoring above the
   auto-match threshold.
5. **Strands resolution agent** — bounded JSON case packet in, recommendation
   out.
6. **Human review** — everything else.

## Hard conflicts (NEVER auto-merge)

- Two distinct results in the same race.
- A confirmed-distinct decision on record.
- Source IDs bound to different canonical entities.
- Impossible grade chronology (e.g., grade decreasing across seasons).
- A locked admin decision.

Confirmed-distinct precision must be **100%** — the feasibility/parity gate
(F7) fails otherwise. `name_corrections.csv` and `DUPLICATE_NAMES_REPORT.md`
form the regression set.

## Resolution agent contract

Input: one bounded JSON case packet (candidate pair + evidence). Output:

```json
{
  "disposition": "MATCH | CREATE | REVIEW",
  "candidate_id": "…",
  "confidence": 0.0,
  "evidence_codes": ["SAME_APPROVED_ALIAS"],
  "conflict_codes": [],
  "summary": "one-paragraph human-readable rationale"
}
```

The agent is stateless (no conversation memory) and advisory: it runs in
review-only mode until its evaluation gate passes; deterministic rules are
the only auto-merge path at rollout.

## Provenance and reversibility

- Every case and decision is recorded in `resolution_cases` /
  `resolution_decisions` with who/what/when/evidence.
- Aliases live in SQLite as authoritative data — never in agent memory.
- Every merge is reversible; unmerge restores the prior entities with audit
  history intact.

## Anti-patterns

- Fuzzy-matching directly into a merge without the ladder.
- Letting the agent see unbounded context (whole rosters) instead of a case
  packet.
- Deleting losing records on merge (merges re-point, never destroy).
