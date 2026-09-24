"""Administrator ingest and resolution-review endpoints (Task 13.3,
design.md section 13's Administrator APIs).

Every route requires the admin role (Requirement 13.3/13.4: enforced on the
server via :func:`~xc_platform.api.deps.require_admin`, denied before any
external call or queued work per Requirement 13.4's "deny it... before
queueing work or incurring external cost").

Maps design.md's ``POST /ingest-runs`` / ``GET /ingest-runs/{id}`` /
``POST /ingest-runs/{id}/commit`` / ``GET /resolution-cases`` /
``POST /resolution-cases/{id}/decisions`` onto the real, tested
:mod:`xc_platform.ingest.workflow` and
:mod:`xc_platform.resolution.decisions` functions. ``approve-scope`` as a
distinct step and ``POST /ingest-runs/{id}/cancel`` are not implemented --
submission runs discovery through staging/resolution in one call, and
cancellation has no dedicated endpoint yet (the same honest gap
tasks.md Task 12.1 already records: no explicit cancel entry point, only
the schema's existing ``failed`` terminal state).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from xc_platform.api.context import AppContext
from xc_platform.api.deps import get_context, require_admin, writer_access
from xc_platform.api.errors import ConflictError, NotFoundError, ValidationError
from xc_platform.api.schemas import (
    CommitResponse,
    Envelope,
    IngestRunResponse,
    IngestSubmitRequest,
)
from xc_platform.db.errors import RepositoryError
from xc_platform.db.publication.republish import publish_writer_state
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.resolution import ResolutionCaseRecord
from xc_platform.ingest.source_router import (
    UnsupportedSourceError,
    normalize_intake_url,
    select_adapter,
)
from xc_platform.ingest.workflow import (
    DiscoveryOutcome,
    IngestRunNotReadyError,
    ResolutionSummary,
    ResolvedStagedResult,
    StagingSummary,
    commit_scope,
    discover_scope,
    resolve_scope,
    stage_scope,
)
from xc_platform.resolution.corrections import rename_school
from xc_platform.resolution.decisions import (
    approve_athlete_match,
    create_new_for_case,
    match_case_to_existing,
    reject_athlete_match,
)
from xc_platform.resolution.exact_match import (
    resolve_athlete_exact,
    resolve_school_exact,
)
from xc_platform.security.session import SessionRecord

router = APIRouter(
    prefix="/api/v1",
    tags=["admin"],
    dependencies=[Depends(require_admin), Depends(writer_access)],
)


def _run_response(ctx: AppContext, ingest_run_id: str) -> IngestRunResponse:
    run = ctx.ingest.get(ingest_run_id)
    if run is None:
        raise NotFoundError(f"no ingest run {ingest_run_id!r}")
    return IngestRunResponse(
        ingest_run_id=run.ingest_run_id,
        state=run.state,
        inserted_count=run.inserted_count,
        quarantined_count=run.quarantined_count,
        error_summary=run.error_summary,
    )


def _resolve_valid_rows(ctx: AppContext, ingest_run_id: str, source_id: str) -> None:
    """Run entity resolution over every currently-``valid`` staged row for
    this ingest run. Safe to call more than once: rows an earlier call
    already auto-matched/auto-created resolve again via the same exact-key
    path (Task 10.1), so this never re-opens a case for an identity
    that's already settled -- only for rows still genuinely unresolved.
    """
    staged_result_ids = tuple(
        row.staged_result_id
        for row in ctx.staging.list_by_ingest_run(
            ingest_run_id, validation_state="valid"
        )
    )
    if not staged_result_ids:
        return
    summary = StagingSummary(
        ingest_run_id=ingest_run_id,
        valid_staged_result_ids=staged_result_ids,
        quarantined_count=0,
        unchanged_count=0,
    )
    resolve_scope(
        summary,
        staging=ctx.staging,
        canonical=CanonicalReadRepository(ctx.writer_conn),
        canonical_write=CanonicalWriteRepository(ctx.writer_conn),
        resolver=ctx.resolver,
        ingest=ctx.ingest,
        source_id=source_id,
    )


@router.post("/ingest-runs", response_model=Envelope)
def submit_ingest_run(
    request: Request, body: IngestSubmitRequest, ctx: AppContext = Depends(get_context)
) -> Envelope:
    """Discover, stage, and resolve a submitted URL in one call.

    Stops at ``awaiting_review`` -- nothing canonical changes until an
    administrator explicitly commits via :func:`commit_ingest_run`
    (Requirement 4's human-in-the-loop gate).
    """
    client = ctx.runsignup_client_factory()
    try:
        outcome: DiscoveryOutcome = discover_scope(
            url=body.url,
            client=client,
            staging=ctx.staging,
            ingest=ctx.ingest,
            correlation_id=request.state.request_id,
        )
    except UnsupportedSourceError as error:
        raise ConflictError(str(error)) from error

    if outcome.importable:
        adapter = select_adapter(normalize_intake_url(body.url), client)
        stage_scope(
            outcome,
            adapter=adapter,
            staging=ctx.staging,
            ingest=ctx.ingest,
            raw_store=ctx.raw_store,
        )
        _resolve_valid_rows(ctx, outcome.ingest_run_id, outcome.source_id)

    return Envelope(
        request_id=request.state.request_id,
        data=_run_response(ctx, outcome.ingest_run_id).model_dump(),
    )


@router.post("/ingest-runs/{ingest_run_id}/resolve", response_model=Envelope)
def resolve_ingest_run(
    request: Request, ingest_run_id: str, ctx: AppContext = Depends(get_context)
) -> Envelope:
    """Re-run resolution after review decisions (Task 19.3). A row whose
    school was unresolved never reached athlete resolution, so settling a
    school case can reveal that row's athlete question; this surfaces it
    now rather than at publish time. Idempotent (see _resolve_valid_rows)."""
    run = ctx.ingest.get(ingest_run_id)
    if run is None:
        raise NotFoundError(f"no ingest run {ingest_run_id!r}")
    if run.state == "awaiting_review":
        _resolve_valid_rows(ctx, ingest_run_id, run.source_id)
    return Envelope(
        request_id=request.state.request_id,
        data={
            **_run_response(ctx, ingest_run_id).model_dump(),
            "pending_cases": len(
                ctx.resolver.list_pending_cases_for_ingest_run(ingest_run_id)
            ),
        },
    )


@router.get("/ingest-runs/{ingest_run_id}", response_model=Envelope)
def get_ingest_run(
    request: Request, ingest_run_id: str, ctx: AppContext = Depends(get_context)
) -> Envelope:
    return Envelope(
        request_id=request.state.request_id,
        data=_run_response(ctx, ingest_run_id).model_dump(),
    )


@router.post("/ingest-runs/{ingest_run_id}/commit", response_model=Envelope)
def commit_ingest_run(
    request: Request, ingest_run_id: str, ctx: AppContext = Depends(get_context)
) -> Envelope:
    """Re-resolve (idempotent for already-settled identities -- see
    :func:`_resolve_valid_rows`) then attempt commit. This is how a commit
    retried after an administrator decides the outstanding resolution
    cases succeeds without re-fetching anything from the source."""
    run = ctx.ingest.get(ingest_run_id)
    if run is None:
        raise NotFoundError(f"no ingest run {ingest_run_id!r}")

    if run.state == "awaiting_review":
        _resolve_valid_rows(ctx, ingest_run_id, run.source_id)

    ready: list[ResolvedStagedResult] = []
    canonical = CanonicalReadRepository(ctx.writer_conn)
    for row in ctx.staging.list_by_ingest_run(ingest_run_id, validation_state="valid"):
        fields = json.loads(row.candidate_fields_json)
        team_name = str(fields.get("team_name") or "")
        first_name, _, last_name = str(fields.get("athlete_full_name") or "").partition(
            " "
        )
        school_id = resolve_school_exact(
            canonical, source_id=row.source_id, raw_school_name=team_name
        )
        athlete_id = resolve_athlete_exact(
            canonical,
            source_id=row.source_id,
            raw_first_name=first_name,
            raw_last_name=last_name or first_name,
            context_key=team_name,
        )
        if school_id is None or athlete_id is None:
            # Still pending review; commit_scope's own gate refuses below
            # if any resolution case is still open.
            continue
        canonical_write = CanonicalWriteRepository(ctx.writer_conn)
        meet_id = canonical_write.get_or_create_meet(
            season_year=int(fields["season_year"]),
            meet_number=int(fields["meet_number"]),
            name=None,
            series=None,
        )
        race_id = canonical_write.get_or_create_race(
            meet_id=meet_id,
            division_code=str(fields.get("division") or "Unknown"),
            gender_code=str(fields.get("gender_code") or "X"),
            distance_meters=fields.get("distance_meters"),
        )
        ready.append(
            ResolvedStagedResult(
                staged_result_id=row.staged_result_id,
                source_result_id=row.source_result_id,
                athlete_id=athlete_id,
                school_id=school_id,
                race_id=race_id,
                candidate_fields=fields,
            )
        )

    resolution_summary = ResolutionSummary(
        ingest_run_id=ingest_run_id, ready=tuple(ready), opened_case_count=0
    )
    try:
        outcome = commit_scope(
            resolution_summary,
            conn=ctx.writer_conn,
            db_path=ctx.db_path,
            source_id=run.source_id,
            resolver=ctx.resolver,
            ingest=ctx.ingest,
            publisher=ctx.snapshot_publisher,
            actor="admin-api",
        )
    except IngestRunNotReadyError as error:
        raise ConflictError(str(error)) from error
    ctx.snapshot_reader.check_now()

    return Envelope(
        request_id=request.state.request_id,
        publication_id=outcome.publication_id,
        data=CommitResponse(
            ingest_run_id=outcome.ingest_run_id,
            publication_id=outcome.publication_id,
            inserted_count=outcome.inserted_count,
            already_committed=outcome.already_committed,
        ).model_dump(),
    )


def _candidate_detail(
    canonical: CanonicalReadRepository, entity_type: str, entity_id: str
) -> dict[str, Any]:
    if entity_type == "school":
        school = canonical.get_school(entity_id)
        return {
            "entity_id": entity_id,
            "display_name": school.display_name if school else entity_id,
            "detail": None,
        }
    athlete = canonical.get_athlete(entity_id)
    schools = {s.school_id: s.display_name for s in canonical.list_schools()}
    parts = []
    for season in canonical.list_athlete_seasons(entity_id):
        label = (
            f"{schools.get(season.school_id, 'unknown school')} {season.season_year}"
        )
        if season.grade:
            label += f" (grade {season.grade})"
        parts.append(label)
    return {
        "entity_id": entity_id,
        "display_name": athlete.display_name if athlete else entity_id,
        "detail": ", ".join(parts) or None,
    }


def _case_view(
    case: ResolutionCaseRecord, canonical: CanonicalReadRepository
) -> dict[str, Any]:
    evidence = json.loads(case.evidence_json or "{}")
    if case.entity_type == "school":
        raw_name = evidence.get("raw_school_name")
        context = None
    else:
        names = (evidence.get("raw_first_name"), evidence.get("raw_last_name"))
        raw_name = " ".join(n for n in names if n) or None
        context = evidence.get("context_key") or None
    id_key = "school_id" if case.entity_type == "school" else "athlete_id"
    ranked = sorted(
        evidence.get("candidates", []), key=lambda c: -float(c.get("score", 0))
    )
    candidates = []
    for candidate in ranked[:5]:
        entity_id = candidate.get(id_key)
        if not entity_id:
            continue
        candidates.append(
            {
                **_candidate_detail(canonical, case.entity_type, entity_id),
                "score": candidate.get("score"),
                "evidence_codes": candidate.get("evidence_codes", []),
                "conflict_codes": candidate.get("conflict_codes", []),
            }
        )
    return {
        "resolution_case_id": case.resolution_case_id,
        "entity_type": case.entity_type,
        "ingest_run_id": case.ingest_run_id,
        "status": case.status,
        "confidence": case.confidence,
        "candidate_entity_id": case.candidate_entity_id,
        "raw_name": raw_name,
        "context": context,
        "season_year": evidence.get("season_year"),
        "reason": evidence.get("reason"),
        "can_decide": bool(evidence.get("source_id")),
        "candidates": candidates,
    }


@router.get("/resolution-cases", response_model=Envelope)
def list_resolution_cases(
    request: Request,
    ingest_run_id: str | None = None,
    ctx: AppContext = Depends(get_context),
) -> Envelope:
    cases = (
        ctx.resolver.list_pending_cases_for_ingest_run(ingest_run_id)
        if ingest_run_id
        else ctx.resolver.list_pending_cases()
    )
    canonical = CanonicalReadRepository(ctx.writer_conn)
    return Envelope(
        request_id=request.state.request_id,
        data=[_case_view(c, canonical) for c in cases],
    )


@router.get("/ingest-runs", response_model=Envelope)
def list_ingest_runs(
    request: Request, limit: int = 25, ctx: AppContext = Depends(get_context)
) -> Envelope:
    runs = ctx.ingest.list_recent(limit=max(1, min(limit, 100)))
    data = [
        {
            **_run_response(ctx, r.ingest_run_id).model_dump(),
            "submitted_url": r.submitted_url,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "result_publication_id": r.result_publication_id,
            "pending_cases": len(
                ctx.resolver.list_pending_cases_for_ingest_run(r.ingest_run_id)
            ),
        }
        for r in runs
    ]
    return Envelope(request_id=request.state.request_id, data=data)


@router.get("/ingest-runs/{ingest_run_id}/preview", response_model=Envelope)
def preview_ingest_run(
    request: Request,
    ingest_run_id: str,
    ctx: AppContext = Depends(get_context),
) -> Envelope:
    """What an administrator reviews before committing (Task 19.3): counts
    by state, the races found, sample rows, and why rows were quarantined."""
    run = ctx.ingest.get(ingest_run_id)
    if run is None:
        raise NotFoundError(f"no ingest run {ingest_run_id!r}")
    by_state: dict[str, int] = {}
    races: dict[tuple[Any, ...], int] = {}
    sample: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    for row in ctx.staging.list_by_ingest_run(ingest_run_id):
        by_state[row.validation_state] = by_state.get(row.validation_state, 0) + 1
        fields = json.loads(row.candidate_fields_json)
        key = (
            fields.get("season_year"),
            fields.get("meet_number"),
            fields.get("division"),
            fields.get("gender_code"),
            fields.get("distance_meters"),
        )
        races[key] = races.get(key, 0) + 1
        compact = {
            "athlete": fields.get("athlete_full_name"),
            "team": fields.get("team_name"),
            "place": fields.get("place_overall"),
            "time": fields.get("original_time_text"),
            "grade": fields.get("grade"),
        }
        if row.validation_state == "quarantined":
            quarantined.append({**compact, "reason": row.validation_warnings_json})
        elif len(sample) < 25:
            sample.append(compact)
    return Envelope(
        request_id=request.state.request_id,
        data={
            "run": {
                **_run_response(ctx, ingest_run_id).model_dump(),
                "submitted_url": run.submitted_url,
                "started_at": run.started_at,
            },
            "counts": by_state,
            "races": [
                {
                    "season_year": k[0],
                    "meet_number": k[1],
                    "division_code": k[2],
                    "gender_code": k[3],
                    "distance_meters": k[4],
                    "rows": n,
                }
                for k, n in sorted(races.items(), key=lambda kv: str(kv[0]))
            ],
            "sample_rows": sample,
            "quarantined_rows": quarantined[:100],
            "pending_cases": len(
                ctx.resolver.list_pending_cases_for_ingest_run(ingest_run_id)
            ),
        },
    )


@router.post(
    "/resolution-cases/{resolution_case_id}/decisions", response_model=Envelope
)
def decide_resolution_case(
    request: Request,
    resolution_case_id: str,
    decision_type: str,
    evidence_summary: str = "",
    actor: str | None = None,
    winner_athlete_id: str | None = None,
    loser_athlete_id: str | None = None,
    entity_id: str | None = None,
    ctx: AppContext = Depends(get_context),
    session: SessionRecord = Depends(require_admin),
) -> Envelope:
    """``reject`` (keep-separate) and ``approve`` (merge two known-duplicate
    athletes) are wired through this endpoint. ``approve`` is scoped
    narrowly (owner decision, tasks.md Task 13.3): it merges two athlete
    IDs the caller already knows are duplicates -- e.g. spotted while
    browsing ``/athletes`` -- not the ingest-time review queue's "is this
    raw name the same as candidate X?" cases, which never create a second
    ("loser") athlete record for this to merge against. That queue stays
    reject-only.
    """
    case = ctx.resolver.get_case(resolution_case_id)
    if case is None:
        raise NotFoundError(f"no resolution case {resolution_case_id!r}")
    # The recorded actor is always the signed-in administrator's
    # pseudonymous id; a client-supplied `actor` is kept only as a label.
    actor = f"admin:{session.actor_id[:12]}" + (f" ({actor})" if actor else "")
    evidence_summary = evidence_summary or f"{decision_type} via admin review"

    if decision_type == "match":
        # Task 19.3: the raw identity is an existing athlete/school.
        if not entity_id:
            raise ValidationError("decision_type 'match' requires entity_id")
        canonical = CanonicalReadRepository(ctx.writer_conn)
        exists = (
            canonical.get_school(entity_id)
            if case.entity_type == "school"
            else canonical.get_athlete(entity_id)
        )
        if exists is None:
            raise NotFoundError(f"no {case.entity_type} {entity_id!r}")
        try:
            decision_id = match_case_to_existing(
                ctx.resolver,
                CanonicalWriteRepository(ctx.writer_conn),
                CanonicalReadRepository(ctx.writer_conn),
                case=case,
                entity_id=entity_id,
                actor=actor,
                evidence_summary=evidence_summary,
            )
        except RepositoryError as error:
            raise ConflictError(str(error)) from error
        return Envelope(
            request_id=request.state.request_id,
            data={"resolution_decision_id": decision_id, "entity_id": entity_id},
        )
    if decision_type == "create_new":
        try:
            decision_id, created_id = create_new_for_case(
                ctx.resolver,
                CanonicalWriteRepository(ctx.writer_conn),
                CanonicalReadRepository(ctx.writer_conn),
                case=case,
                actor=actor,
                evidence_summary=evidence_summary,
            )
        except RepositoryError as error:
            raise ConflictError(str(error)) from error
        return Envelope(
            request_id=request.state.request_id,
            data={"resolution_decision_id": decision_id, "entity_id": created_id},
        )

    if decision_type == "reject":
        try:
            decision_id = reject_athlete_match(
                ctx.resolver,
                resolution_case_id=resolution_case_id,
                actor=actor,
                evidence_summary=evidence_summary,
            )
        except RepositoryError as error:
            raise ConflictError(str(error)) from error
    elif decision_type == "approve":
        if not winner_athlete_id or not loser_athlete_id:
            raise ValidationError(
                "decision_type 'approve' requires winner_athlete_id and "
                "loser_athlete_id"
            )
        if winner_athlete_id == loser_athlete_id:
            raise ValidationError("winner_athlete_id and loser_athlete_id must differ")
        canonical = CanonicalReadRepository(ctx.writer_conn)
        if canonical.get_athlete(winner_athlete_id) is None:
            raise NotFoundError(f"no athlete {winner_athlete_id!r}")
        if canonical.get_athlete(loser_athlete_id) is None:
            raise NotFoundError(f"no athlete {loser_athlete_id!r}")
        try:
            decision_id = approve_athlete_match(
                ctx.resolver,
                CanonicalWriteRepository(ctx.writer_conn),
                resolution_case_id=resolution_case_id,
                winner_athlete_id=winner_athlete_id,
                loser_athlete_id=loser_athlete_id,
                actor=actor,
                evidence_summary=evidence_summary,
            )
        except RepositoryError as error:
            raise ConflictError(str(error)) from error
    else:
        raise ConflictError(
            f"decision_type {decision_type!r} is not supported by this endpoint "
            "(use 'match', 'create_new', 'reject', or 'approve')"
        )

    return Envelope(
        request_id=request.state.request_id,
        data={"resolution_decision_id": decision_id},
    )


class SchoolRenameRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=3, max_length=500)


@router.post("/schools/{school_id}/rename", response_model=Envelope)
def rename_school_route(
    request: Request,
    school_id: str,
    body: SchoolRenameRequest,
    ctx: AppContext = Depends(get_context),
    session: SessionRecord = Depends(require_admin),
) -> Envelope:
    """Owner correction (Task 19.3): rename a school and publish the
    change. Results, rosters, and aliases keep pointing at the same school;
    the decision history records who changed what and why."""
    if CanonicalReadRepository(ctx.writer_conn).get_school(school_id) is None:
        raise NotFoundError(f"no school {school_id!r}")
    try:
        decision_id = rename_school(
            ctx.resolver,
            CanonicalWriteRepository(ctx.writer_conn),
            school_id=school_id,
            new_name=body.name,
            actor=f"admin:{session.actor_id[:12]}",
            reason=body.reason,
        )
    except RepositoryError as error:
        raise ConflictError(str(error)) from error
    manifest = publish_writer_state(
        ctx.writer_conn,
        ctx.db_path,
        ctx.snapshot_publisher,
        created_by=f"admin:{session.actor_id[:12]}",
        label=f"correction-{decision_id}",
    )
    ctx.snapshot_reader.check_now()
    return Envelope(
        request_id=request.state.request_id,
        publication_id=manifest.publication_id,
        data={
            "school_id": school_id,
            "display_name": body.name.strip(),
            "resolution_decision_id": decision_id,
        },
    )
