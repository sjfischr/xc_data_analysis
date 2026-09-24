"""Owner corrections to canonical entities (Task 19.3).

A correction changes what the league's data says about an entity, so it
leaves the same kind of trail a review decision does: a resolution case
plus an ``approve`` decision naming the administrator, the reason, and the
before/after values (Requirement 8.6's "decision, actor, timestamp, evidence
summary, and affected records"). The first correction, 2026-09-24: the
school recorded since 2023 as "St John the Evangelist" is St. John the
Beloved (McLean, VA; Diocese of Arlington) -- the 2026 RunSignup results
use the correct name.
"""

from __future__ import annotations

import json

from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.resolution import ResolutionRepository


def rename_school(
    resolver: ResolutionRepository,
    writer: CanonicalWriteRepository,
    *,
    school_id: str,
    new_name: str,
    actor: str,
    reason: str,
) -> str:
    """Rename a school everywhere it is shown; every result, roster row,
    and alias stays with the same school. Returns the decision id."""
    old_canonical, old_display = writer.rename_school(
        school_id=school_id, new_name=new_name
    )
    change = {
        "action": "rename_school",
        "school_id": school_id,
        "from_canonical_name": old_canonical,
        "from_display_name": old_display,
        "to": new_name.strip(),
    }
    case_id = resolver.open_case(
        entity_type="school",
        candidate_entity_id=school_id,
        confidence=1.0,
        evidence_json=json.dumps({**change, "reason": reason}),
    )
    return resolver.record_decision(
        resolution_case_id=case_id,
        decision_type="approve",
        actor=actor,
        evidence_summary=reason,
        affected_records_json=json.dumps(change),
    )


def merge_schools(
    resolver: ResolutionRepository,
    writer: CanonicalWriteRepository,
    *,
    winner_school_id: str,
    loser_school_id: str,
    actor: str,
    reason: str,
) -> str:
    """Fold a duplicate school into the one that keeps its name; the moved
    record ids go into the decision so the merge can be traced or undone.
    First use, 2026-09-24: "St. Rita Parish Alexandria" (2023) and "St
    Rita" (2024 on) are one school. Returns the decision id."""
    moved = writer.merge_schools(
        winner_school_id=winner_school_id, loser_school_id=loser_school_id
    )
    change = {
        "action": "merge_schools",
        "winner_school_id": winner_school_id,
        "loser_school_id": loser_school_id,
        "moved": moved,
    }
    case_id = resolver.open_case(
        entity_type="school",
        candidate_entity_id=winner_school_id,
        confidence=1.0,
        evidence_json=json.dumps(
            {
                "action": "merge_schools",
                "winner_school_id": winner_school_id,
                "loser_school_id": loser_school_id,
                "reason": reason,
            }
        ),
    )
    return resolver.record_decision(
        resolution_case_id=case_id,
        decision_type="approve",
        actor=actor,
        evidence_summary=reason,
        affected_records_json=json.dumps(change),
    )
