"""Human disposition of a pending resolution case (Task 10.3): approving a
match merges two canonical athletes, and reversing that decision undoes
exactly that merge (Requirement 8.10).

The deterministic workflow (:mod:`xc_platform.resolution.workflow`) only
ever opens cases or resolves automatically -- it never merges anything by
itself (design.md rollout policy: fuzzy/agent proposals are review-only).
Everything in this module is the human (or, once Task 11.4/an
owner-enabled auto-match threshold exists, the agent-and-then-human) side
of that review queue, always going through
:class:`~xc_platform.db.repositories.resolution.ResolutionRepository` so
SQLite stays the sole authority (Requirement 8.8).
"""

from __future__ import annotations

import json
from typing import Any

from xc_platform.db.errors import RepositoryError
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import (
    AthleteMergeRecord,
    CanonicalWriteRepository,
)
from xc_platform.db.repositories.resolution import (
    ResolutionCaseRecord,
    ResolutionRepository,
)
from xc_platform.migration.aliases import normalize_alias_value


def approve_athlete_match(
    resolver: ResolutionRepository,
    writer: CanonicalWriteRepository,
    *,
    resolution_case_id: str,
    winner_athlete_id: str,
    loser_athlete_id: str,
    actor: str,
    evidence_summary: str,
    policy_version: str | None = None,
) -> str:
    """Merge ``loser_athlete_id`` into ``winner_athlete_id`` and record the
    approval. Returns the new ``resolution_decision_id``."""
    merge_record = writer.merge_athletes(
        winner_athlete_id=winner_athlete_id, loser_athlete_id=loser_athlete_id
    )
    return resolver.record_decision(
        resolution_case_id=resolution_case_id,
        decision_type="approve",
        actor=actor,
        evidence_summary=evidence_summary,
        affected_records_json=merge_record.to_json(),
        policy_version=policy_version,
    )


def reverse_athlete_match(
    resolver: ResolutionRepository,
    writer: CanonicalWriteRepository,
    *,
    resolution_case_id: str,
    approve_decision_id: str,
    approve_affected_records_json: str,
    actor: str,
    evidence_summary: str,
) -> str:
    """Undo a previously approved merge, restoring the loser's identity and
    every result/season/alias it moved. Returns the new
    ``resolution_decision_id``.

    ``approve_affected_records_json`` must be the exact
    ``affected_records_json`` the approving decision recorded -- callers
    read it off that ``ResolutionCaseRecord``'s decision history, never
    reconstruct it, so a reversal only ever moves back rows a specific
    merge actually moved (Requirement 8.10).
    """
    merge_record = AthleteMergeRecord.from_json(approve_affected_records_json)
    writer.reverse_athlete_merge(merge_record)
    return resolver.record_decision(
        resolution_case_id=resolution_case_id,
        decision_type="reverse",
        actor=actor,
        evidence_summary=evidence_summary,
        affected_records_json=approve_affected_records_json,
        reversal_of_decision_id=approve_decision_id,
    )


def reject_athlete_match(
    resolver: ResolutionRepository,
    *,
    resolution_case_id: str,
    actor: str,
    evidence_summary: str,
) -> str:
    """Confirm the candidate is a different person -- no merge happens.
    This is the "keep-separate" action (design.md 10.1/Requirement 8.9):
    it leaves both athletes untouched and becomes the confirmed-distinct
    signal ``CanonicalReadRepository.athlete_confirmed_distinct`` checks
    on every future resolution attempt."""
    return resolver.record_decision(
        resolution_case_id=resolution_case_id,
        decision_type="reject",
        actor=actor,
        evidence_summary=evidence_summary,
        affected_records_json="[]",
    )


# --- Task 19.3: ingest-time review decisions --------------------------------
#
# An ingest-time case asks "is this raw name (from this source) the same as
# an existing athlete/school?". Nothing was created for it, so there is no
# second record to merge -- the answer is an alias. Both decisions below
# write the exact alias :mod:`xc_platform.resolution.exact_match` looks up,
# so the next resolve pass auto-matches the row and the case never reopens.
#
# Both are safe to retry. Found in local testing (2026-09-24): a decision
# that failed after writing its alias (a concurrent request broke the
# shared writer connection mid-decision) could never be retried -- every
# retry hit the alias uniqueness constraint and left another orphan
# athlete behind. An alias that already exists for the same identity is
# now reused instead of re-inserted.


def _case_evidence(case: ResolutionCaseRecord) -> dict[str, Any]:
    evidence = json.loads(case.evidence_json or "{}")
    if not evidence.get("source_id"):
        raise RepositoryError(
            f"case {case.resolution_case_id!r} has no source_id in its evidence "
            "(opened before Task 19.3); reject it and re-run the intake instead"
        )
    return dict(evidence)


def _raw_name(case: ResolutionCaseRecord, evidence: dict[str, Any]) -> str:
    if case.entity_type == "school":
        return str(evidence["raw_school_name"])
    return f"{evidence['raw_first_name']} {evidence['raw_last_name']}".strip()


def _existing_alias_target(
    canonical: CanonicalReadRepository,
    case: ResolutionCaseRecord,
    evidence: dict[str, Any],
) -> str | None:
    """The entity this raw identity is already aliased to, if any."""
    normalized = normalize_alias_value(_raw_name(case, evidence))
    if case.entity_type == "school":
        return canonical.resolve_school_alias(
            source_id=evidence["source_id"], normalized_value=normalized
        )
    return canonical.resolve_athlete_alias(
        source_id=evidence["source_id"],
        normalized_value=normalized,
        context_key=str(evidence.get("context_key") or ""),
    )


def _add_alias(
    writer: CanonicalWriteRepository,
    case: ResolutionCaseRecord,
    evidence: dict[str, Any],
    entity_id: str,
) -> str:
    raw = _raw_name(case, evidence)
    if case.entity_type == "school":
        return writer.add_school_alias(
            school_id=entity_id,
            source_id=evidence["source_id"],
            raw_value=raw,
            normalized_value=normalize_alias_value(raw),
        )
    return writer.add_athlete_alias(
        athlete_id=entity_id,
        source_id=evidence["source_id"],
        raw_value=raw,
        normalized_value=normalize_alias_value(raw),
        context_key=str(evidence.get("context_key") or ""),
    )


def match_case_to_existing(
    resolver: ResolutionRepository,
    writer: CanonicalWriteRepository,
    canonical: CanonicalReadRepository,
    *,
    case: ResolutionCaseRecord,
    entity_id: str,
    actor: str,
    evidence_summary: str,
) -> str:
    """The raw identity IS ``entity_id`` (usually the case's candidate, but
    any existing athlete/school the reviewer picks). Records an
    ``approve``."""
    evidence = _case_evidence(case)
    existing = _existing_alias_target(canonical, case, evidence)
    if existing is not None and existing != entity_id:
        raise RepositoryError(
            f"{_raw_name(case, evidence)!r} is already linked to a different "
            f"{case.entity_type} ({existing}); undo that link first"
        )
    alias_id = (
        _add_alias(writer, case, evidence, entity_id) if existing is None else None
    )
    return resolver.record_decision(
        resolution_case_id=case.resolution_case_id,
        decision_type="approve",
        actor=actor,
        evidence_summary=evidence_summary,
        affected_records_json=json.dumps(
            {
                "action": "match_to_existing",
                "entity_type": case.entity_type,
                "entity_id": entity_id,
                "alias_id": alias_id,
                "alias_reused": existing is not None,
            }
        ),
    )


def create_new_for_case(
    resolver: ResolutionRepository,
    writer: CanonicalWriteRepository,
    canonical: CanonicalReadRepository,
    *,
    case: ResolutionCaseRecord,
    actor: str,
    evidence_summary: str,
) -> tuple[str, str]:
    """The raw identity is someone/somewhere new: create the athlete or
    school with its alias. Records a ``reject`` of the candidate, which for
    athletes is also the confirmed-distinct signal. If an earlier attempt
    already created the record and alias, that record is reused rather than
    duplicated. Returns ``(decision_id, entity_id)``."""
    evidence = _case_evidence(case)
    existing = _existing_alias_target(canonical, case, evidence)
    alias_id: str | None = None
    if existing is not None:
        entity_id = existing
    else:
        raw = _raw_name(case, evidence)
        if case.entity_type == "school":
            entity_id = writer.create_school(canonical_name=raw)
        else:
            entity_id = writer.create_athlete(display_name=raw)
        alias_id = _add_alias(writer, case, evidence, entity_id)
    decision_id = resolver.record_decision(
        resolution_case_id=case.resolution_case_id,
        decision_type="reject",
        actor=actor,
        evidence_summary=evidence_summary,
        affected_records_json=json.dumps(
            {
                "action": "create_new",
                "entity_type": case.entity_type,
                "entity_id": entity_id,
                "alias_id": alias_id,
                "alias_reused": existing is not None,
            }
        ),
    )
    return decision_id, entity_id
