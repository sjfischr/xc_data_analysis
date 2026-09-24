"""End-to-end intake workflow orchestration (Task 12, design.md section 9.5).

Composes pieces already built in Tasks 4-10 (source router, RunSignup
adapter, staging/ingest/canonical/resolution repositories, entity
resolution workflow, S3 publisher) into the actual pipeline a submitted
URL goes through: discover -> stage -> resolve -> commit/publish.

design.md 9.5 lists a finer-grained state sequence (SUBMITTED ->
DISCOVERING -> FETCHING -> STAGED -> VALIDATING -> RESOLVING ->
AWAITING_REVIEW -> READY_TO_COMMIT -> COMMITTING -> PUBLISHED) than the
``ingest_runs.state`` CHECK constraint Task 4.4's schema already fixed
(``pending -> discovering -> staging -> awaiting_review -> committing ->
committed -> failed -> rolled_back``). This module maps the finer states
onto the coarser ones it actually has to work with: ``discovering`` covers
SUBMITTED/DISCOVERING/FETCHING, ``staging`` covers STAGED/VALIDATING/
RESOLVING, and ``committing`` covers READY_TO_COMMIT/COMMITTING. The schema
has no separate CANCELLED value; cancellation is implemented as a
transition to ``failed`` with an explanatory ``error_summary`` -- the same
observable "stopped, explained, no partial change" outcome, under the
existing terminal state.

Queueing a submission onto an idempotent FIFO message (design.md 9.5's "one
idempotent FIFO message") is AWS infrastructure (SQS, Task 15.1) outside
this module's scope. This module IS the job a queue consumer would run for
one message -- callable directly from a CLI, a test, or a future
Lambda/worker without a real queue existing.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from xc_platform.db.identifiers import new_id, utc_now_iso
from xc_platform.db.publication.writer import SnapshotPublisher
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.db.repositories.ingest import IngestRunRepository
from xc_platform.db.repositories.publications import PublicationRepository
from xc_platform.db.repositories.resolution import ResolutionRepository
from xc_platform.db.repositories.staging import StagingRepository
from xc_platform.ingest.adapters.runsignup_client import RunSignupClient
from xc_platform.ingest.contract import (
    DiscoveredResultSet,
    DiscoveryRequest,
    SourceAdapter,
)
from xc_platform.ingest.raw_storage import RawObjectStore
from xc_platform.ingest.source_router import normalize_intake_url, select_adapter
from xc_platform.resolution.evidence import AthleteResolutionInput
from xc_platform.resolution.workflow import (
    DISPOSITION_AUTO_CREATED,
    DISPOSITION_AUTO_MATCHED,
    ResolutionOutcome,
    resolve_athlete,
    resolve_school,
)

RUNSIGNUP_BASE_DOMAIN = "runsignup.com"


class IngestRunNotReadyError(RuntimeError):
    """Raised by :func:`commit_scope` when the run has open resolution
    cases or is not in the ``awaiting_review`` state -- commit refuses
    rather than silently skipping the unresolved rows."""


# --- 12.1: submission and discovery ----------------------------------


@dataclass(frozen=True, slots=True)
class DiscoveryOutcome:
    ingest_run_id: str
    source_id: str
    importable: tuple[DiscoveredResultSet, ...]
    frozen: tuple[DiscoveredResultSet, ...]


def discover_scope(
    *,
    url: str,
    client: RunSignupClient,
    staging: StagingRepository,
    ingest: IngestRunRepository,
    correlation_id: str,
) -> DiscoveryOutcome:
    """Normalize the submitted URL, create the ingest run, and discover its
    scope. Raises :class:`~xc_platform.ingest.source_router.UnsupportedSourceError`
    for a URL release 1 has no adapter for -- no ingest run is created in
    that case, since there is nothing to track.

    If every discovered item resolves to a frozen season, the run is
    transitioned straight to ``failed`` with an explanatory
    ``error_summary`` and no staging happens (Requirement 1.8/4.9's frozen
    season refusal, Task 12.5's explicit "refused with an explanatory
    status and no staging or canonical change").
    """
    normalized = normalize_intake_url(url)
    adapter = select_adapter(normalized, client)
    source_id = staging.get_or_create_source(
        source_namespace=normalized.source_namespace,
        adapter_type="runsignup",
        base_domain=RUNSIGNUP_BASE_DOMAIN,
    )
    ingest_run_id = ingest.create_run(
        source_id=source_id,
        submitted_url=url,
        adapter_type="runsignup",
        correlation_id=correlation_id,
    )
    ingest.transition(ingest_run_id, "discovering")

    try:
        result = adapter.discover(
            DiscoveryRequest(normalized_url=normalized, correlation_id=correlation_id)
        )
    except Exception as error:
        ingest.transition(
            ingest_run_id, "failed", error_summary=f"discovery failed: {error}"
        )
        raise

    if not result.importable:
        ingest.transition(
            ingest_run_id,
            "failed",
            error_summary=(
                "every discovered result set resolves to a frozen season "
                f"({sorted({item.season_year for item in result.frozen})}); "
                "frozen seasons are discoverable but never imported "
                "(Requirement 1.8/4.9)"
            )
            if result.frozen
            else "no result sets were discovered for this URL",
        )

    return DiscoveryOutcome(
        ingest_run_id=ingest_run_id,
        source_id=source_id,
        importable=result.importable,
        frozen=result.frozen,
    )


# --- 12.2: staging and validation --------------------------------------


@dataclass(frozen=True, slots=True)
class StagingSummary:
    ingest_run_id: str
    valid_staged_result_ids: tuple[str, ...]
    quarantined_count: int
    unchanged_count: int


def stage_scope(
    outcome: DiscoveryOutcome,
    *,
    adapter: SourceAdapter,
    staging: StagingRepository,
    ingest: IngestRunRepository,
    raw_store: RawObjectStore,
) -> StagingSummary:
    """Fetch, normalize, and stage every importable item. Never touches
    canonical tables (Requirement 7.1-7.6) -- only ``source_objects`` and
    ``staged_results``.

    An item whose exact idempotency key was already staged and is
    ``valid``/``committed`` is skipped as unchanged (Requirement 4.6):
    re-running the same import produces zero duplicate staged rows. A
    matching idempotency key with *different* candidate fields than what
    was previously committed is staged but immediately quarantined,
    flagging the conflict for administrator review rather than silently
    overwriting (Requirement 3.4-3.9's "preserve both source versions...
    require approved replacement rules").
    """
    ingest.transition(outcome.ingest_run_id, "staging")

    valid_ids: list[str] = []
    quarantined = 0
    unchanged = 0

    for item in outcome.importable:
        try:
            fetch_result = adapter.fetch(item)
        except Exception as error:
            ingest.transition(
                outcome.ingest_run_id,
                "failed",
                error_summary=(
                    f"extraction failed for race {item.race_id} event {item.event_id} "
                    f"set {item.set_id}: {error}"
                ),
            )
            raise
        raw = raw_store.store_fetch_result(
            source_namespace=item.source_namespace, item=item, fetch_result=fetch_result
        )
        source_object_id = staging.record_source_object(
            source_id=outcome.source_id,
            ingest_run_id=outcome.ingest_run_id,
            source_url=f"https://runsignup.com/Race/Results/{item.race_id}",
            raw_s3_key=raw.raw_s3_key,
            content_sha256=raw.content_sha256,
            byte_size=raw.byte_size,
            media_type=raw.media_type,
            extraction_strategy="runsignup_rest",
        )

        for staged_input in adapter.normalize(fetch_result):
            existing = staging.find_by_idempotency_key(
                source_id=outcome.source_id,
                idempotency_key=staged_input.idempotency_key,
            )
            if existing is not None and existing.validation_state in (
                "valid",
                "committed",
            ):
                if existing.candidate_fields_json == json.dumps(
                    staged_input.candidate_fields, sort_keys=True, default=str
                ):
                    unchanged += 1
                    continue

            staged_result_id = staging.stage_result(
                ingest_run_id=outcome.ingest_run_id,
                source_object_id=source_object_id,
                source_id=outcome.source_id,
                idempotency_key=staged_input.idempotency_key,
                raw_fields_json=json.dumps(
                    staged_input.raw_fields, sort_keys=True, default=str
                ),
                candidate_fields_json=json.dumps(
                    staged_input.candidate_fields, sort_keys=True, default=str
                ),
                source_result_id=staged_input.source_result_id,
            )

            if not staged_input.is_valid:
                staging.quarantine(
                    staged_result_id, reason="; ".join(staged_input.validation_warnings)
                )
                quarantined += 1
            elif existing is not None and existing.validation_state == "committed":
                staging.quarantine(
                    staged_result_id,
                    reason=(
                        f"conflicts with previously committed staged_result "
                        f"{existing.staged_result_id}: candidate fields changed "
                        "since the last commit; requires administrator review"
                    ),
                )
                quarantined += 1
            else:
                staging.mark_valid(staged_result_id)
                valid_ids.append(staged_result_id)

    ingest.record_counts(
        outcome.ingest_run_id, quarantined=quarantined, unchanged=unchanged
    )

    return StagingSummary(
        ingest_run_id=outcome.ingest_run_id,
        valid_staged_result_ids=tuple(valid_ids),
        quarantined_count=quarantined,
        unchanged_count=unchanged,
    )


# --- 12.3: deterministic + agent resolution ----------------------------


@dataclass(frozen=True, slots=True)
class ResolvedStagedResult:
    staged_result_id: str
    source_result_id: str | None
    athlete_id: str
    school_id: str
    race_id: str
    candidate_fields: dict[str, object]


@dataclass(frozen=True, slots=True)
class ResolutionSummary:
    ingest_run_id: str
    ready: tuple[ResolvedStagedResult, ...]
    opened_case_count: int


def _open_case(
    resolver: ResolutionRepository,
    *,
    entity_type: str,
    ingest_run_id: str,
    staged_result_id: str,
    reason: str,
) -> None:
    resolver.open_case(
        entity_type=entity_type,
        ingest_run_id=ingest_run_id,
        evidence_json=json.dumps(
            {"staged_result_id": staged_result_id, "reason": reason}
        ),
    )


def resolve_scope(
    summary: StagingSummary,
    *,
    staging: StagingRepository,
    canonical: CanonicalReadRepository,
    canonical_write: CanonicalWriteRepository,
    resolver: ResolutionRepository,
    ingest: IngestRunRepository,
    source_id: str,
) -> ResolutionSummary:
    """Resolve meet/race, school, and athlete identity for every valid
    staged row.

    The meet/race is always a deterministic get-or-create (design.md 10.1
    step 1's "stable upstream source entity link" territory -- a meet
    number within a season and a division/gender within that meet fully
    determine the race; there is no fuzzy meet-matching problem here, per
    :mod:`xc_platform.resolution.evidence`'s module docstring). School and
    athlete identity go through the real deterministic resolution workflow
    (Task 10), which auto-resolves, auto-creates, or opens a review case.

    Every successful staging run passes through ``awaiting_review`` before
    ``committing`` (the schema's own state machine requires it), whether or
    not any case was actually opened -- so this always transitions there,
    and :func:`commit_scope` is the gate that checks whether anything is
    still pending.
    """
    opened = 0
    ready: list[ResolvedStagedResult] = []
    rows = staging.list_by_ingest_run(summary.ingest_run_id, validation_state="valid")
    rows_by_id = {r.staged_result_id: r for r in rows}

    # A meet's results are dozens-to-hundreds of rows from a handful of
    # schools; without this cache, every row for the same school
    # independently called resolve_school and opened its own review case
    # for the identical decision -- one small developmental meet produced
    # well over a hundred duplicate "is this St. Agnes?" cases for the
    # same two or three schools (found live during Task 18.2's production
    # validation, 2026-09-23, against a real current-season meet). Keyed
    # on the exact raw string: this only collapses byte-identical repeats
    # within one ingest run, never changes which candidate anything scores
    # against.
    school_outcomes: dict[str, ResolutionOutcome] = {}

    for staged_result_id in summary.valid_staged_result_ids:
        row = rows_by_id[staged_result_id]
        fields = json.loads(row.candidate_fields_json)

        team_name = fields.get("team_name")
        if not team_name:
            _open_case(
                resolver,
                entity_type="school",
                ingest_run_id=summary.ingest_run_id,
                staged_result_id=staged_result_id,
                reason="no team_name",
            )
            opened += 1
            continue
        team_name = str(team_name)
        if team_name in school_outcomes:
            school_outcome = school_outcomes[team_name]
        else:
            school_outcome = resolve_school(
                canonical,
                canonical_write,
                resolver,
                source_id=source_id,
                ingest_run_id=summary.ingest_run_id,
                raw_school_name=team_name,
            )
            school_outcomes[team_name] = school_outcome
        if school_outcome.disposition not in (
            DISPOSITION_AUTO_MATCHED,
            DISPOSITION_AUTO_CREATED,
        ):
            opened += 1
            continue

        full_name = fields.get("athlete_full_name")
        if not full_name:
            _open_case(
                resolver,
                entity_type="athlete",
                ingest_run_id=summary.ingest_run_id,
                staged_result_id=staged_result_id,
                reason="no athlete name",
            )
            opened += 1
            continue

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

        first_name, _, last_name = str(full_name).partition(" ")
        athlete_outcome = resolve_athlete(
            canonical,
            canonical_write,
            resolver,
            source_id=source_id,
            ingest_run_id=summary.ingest_run_id,
            incoming=AthleteResolutionInput(
                first_name=first_name,
                last_name=last_name or first_name,
                school_id=school_outcome.entity_id,
                season_year=int(fields["season_year"]),
                grade=fields.get("grade"),
                gender_code=fields.get("gender_code"),
                bib=fields.get("bib"),
                race_id=race_id,
            ),
            raw_first_name=first_name,
            raw_last_name=last_name or first_name,
            context_key=str(team_name),
        )
        if athlete_outcome.disposition not in (
            DISPOSITION_AUTO_MATCHED,
            DISPOSITION_AUTO_CREATED,
        ):
            opened += 1
            continue

        if school_outcome.entity_id is None or athlete_outcome.entity_id is None:
            raise RuntimeError(
                "resolve_school/resolve_athlete returned an auto_matched/auto_created "
                "disposition with entity_id=None; this is a resolution workflow bug, "
                "not a normal runtime condition"
            )
        ready.append(
            ResolvedStagedResult(
                staged_result_id=staged_result_id,
                source_result_id=row.source_result_id,
                athlete_id=athlete_outcome.entity_id,
                school_id=school_outcome.entity_id,
                race_id=race_id,
                candidate_fields=fields,
            )
        )

    # Idempotent: resolve_scope may legitimately run more than once for the
    # same ingest run (an administrator decides a case, then a caller
    # re-resolves to pick up rows that are now settled) -- the state
    # machine's own 'awaiting_review' -> 'awaiting_review' is not itself an
    # allowed transition, so only transition when actually leaving 'staging'.
    current = ingest.get(summary.ingest_run_id)
    if current is not None and current.state == "staging":
        ingest.transition(summary.ingest_run_id, "awaiting_review")
    return ResolutionSummary(
        ingest_run_id=summary.ingest_run_id,
        ready=tuple(ready),
        opened_case_count=opened,
    )


# --- 12.4: transactional commit and publication -------------------------


@dataclass(frozen=True, slots=True)
class CommitOutcome:
    ingest_run_id: str
    publication_id: str
    inserted_count: int
    already_committed: bool


def commit_scope(
    resolution_summary: ResolutionSummary,
    *,
    conn: sqlite3.Connection,
    db_path: Path,
    source_id: str,
    resolver: ResolutionRepository,
    ingest: IngestRunRepository,
    publisher: SnapshotPublisher,
    actor: str,
) -> CommitOutcome:
    """Revalidate readiness, apply every ready row in one SQLite
    transaction, and publish the result.

    Idempotent (Requirement 4.5): a repeated commit request against an
    already-``committed`` run returns the existing outcome without
    re-applying anything -- it never re-inserts or double-publishes.
    Refuses (raises :class:`IngestRunNotReadyError`) rather than silently
    partially committing when the run is not in ``awaiting_review`` or
    still has open resolution cases (Task 12.3's gate).
    """
    run = ingest.get(resolution_summary.ingest_run_id)
    if run is None:
        raise IngestRunNotReadyError(
            f"no ingest run {resolution_summary.ingest_run_id!r}"
        )

    if run.state == "committed":
        if run.result_publication_id is None:
            raise IngestRunNotReadyError(
                f"ingest run {run.ingest_run_id!r} is committed but has no "
                "result_publication_id recorded -- this is a data integrity bug, "
                "not a normal idempotent-repeat case"
            )
        return CommitOutcome(
            ingest_run_id=run.ingest_run_id,
            publication_id=run.result_publication_id,
            inserted_count=run.inserted_count,
            already_committed=True,
        )

    if run.state != "awaiting_review":
        raise IngestRunNotReadyError(
            f"ingest run {run.ingest_run_id!r} is in state {run.state!r}, "
            "not 'awaiting_review'"
        )

    pending_cases = resolver.list_pending_cases_for_ingest_run(run.ingest_run_id)
    if pending_cases:
        raise IngestRunNotReadyError(
            f"{len(pending_cases)} resolution case(s) still pending for ingest "
            f"run {run.ingest_run_id!r}; cannot commit until all are decided"
        )

    ingest.transition(run.ingest_run_id, "committing")

    # A single explicit transaction for the whole apply (Requirement 4.5).
    # This deliberately does NOT call canonical_write.insert_result /
    # staging.mark_committed -- each of those opens its own transaction via
    # BaseRepository.transaction(), and SQLite raises on a nested BEGIN.
    # The SQL below is intentionally identical to those methods'.
    inserted = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        now = utc_now_iso()
        for item in resolution_summary.ready:
            fields = item.candidate_fields
            conn.execute(
                "INSERT INTO results (result_id, source_id, source_result_id, "
                "race_id, athlete_id, school_id, ingest_run_id, finish_time_ms, "
                "original_time_text, place_overall, bib, grade, scored_flag, "
                "created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(),
                    source_id,
                    item.source_result_id,
                    item.race_id,
                    item.athlete_id,
                    item.school_id,
                    run.ingest_run_id,
                    fields.get("finish_time_ms"),
                    fields.get("original_time_text"),
                    fields.get("place_overall"),
                    fields.get("bib"),
                    fields.get("grade"),
                    str(fields.get("scored_flag") or "unknown"),
                    now,
                    now,
                ),
            )
            conn.execute(
                "UPDATE staged_results SET validation_state = 'committed' "
                "WHERE staged_result_id = ?",
                (item.staged_result_id,),
            )
            # Task 19.3: an athlete matched to an existing record (rather
            # than newly created, which already writes its season) still
            # needs this season's roster row, or they vanish from school
            # rosters and season history. Idempotent via
            # ux_athlete_seasons(athlete_id, season_year, school_id).
            gender_code = fields.get("gender_code")
            if gender_code in ("F", "M", "X"):
                conn.execute(
                    "INSERT OR IGNORE INTO athlete_seasons (athlete_season_id, "
                    "athlete_id, season_year, school_id, grade, gender_code, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        new_id(),
                        item.athlete_id,
                        int(str(fields["season_year"])),
                        item.school_id,
                        fields.get("grade"),
                        gender_code,
                        now,
                        now,
                    ),
                )
            inserted += 1
        conn.execute("COMMIT")
    except Exception as error:
        conn.execute("ROLLBACK")
        ingest.transition(
            run.ingest_run_id,
            "failed",
            error_summary=f"commit transaction failed: {error}",
        )
        raise

    ingest.record_counts(run.ingest_run_id, inserted=inserted)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # The local transaction above already committed -- these rows are real
    # and permanent. A failure from here on is a *publication* failure, not
    # a data-integrity one: the S3 publisher's own compare-and-swap (Task
    # 6) guarantees a reader never observes a half-written snapshot either
    # way. There is no "retry publish only" state in this schema's state
    # machine (`failed` is terminal, Task 4.4), so this is recorded
    # honestly as failed rather than silently left in `committing` --
    # recovering the already-committed local rows into a snapshot requires
    # a follow-up publish (administrator/CLI action), not an automatic
    # retry of this same ingest run.
    publications = PublicationRepository(conn)
    parent = publications.get_active()
    try:
        manifest = publisher.publish(
            db_path,
            created_by=actor,
            ingest_run_id=run.ingest_run_id,
            summary={"inserted": inserted, "quarantined": run.quarantined_count},
            parent_publication_id=parent.publication_id if parent else None,
        )
    except Exception as error:
        ingest.transition(
            run.ingest_run_id,
            "failed",
            error_summary=(
                f"{inserted} result(s) committed locally, "
                f"but publication failed: {error}"
            ),
        )
        raise

    # Bookkeeping about a publication that already happened (this table's
    # own docstring) -- S3's active.json, not this row, is authoritative
    # for "what to open"; this just lets the local database answer "how did
    # I get here" without a round trip to S3.
    publications.record_publication(
        publication_id=manifest.publication_id,
        snapshot_key=manifest.snapshot_key,
        snapshot_version_id=manifest.snapshot_version_id,
        content_sha256=manifest.sha256,
        byte_size=manifest.byte_size,
        schema_version=manifest.schema_version,
        summary_json=json.dumps(manifest.summary, sort_keys=True),
        parent_publication_id=manifest.parent_publication_id,
        created_by_ingest_run_id=run.ingest_run_id,
    )
    ingest.attach_publication(run.ingest_run_id, manifest.publication_id)
    ingest.transition(run.ingest_run_id, "committed")

    return CommitOutcome(
        ingest_run_id=run.ingest_run_id,
        publication_id=manifest.publication_id,
        inserted_count=inserted,
        already_committed=False,
    )
