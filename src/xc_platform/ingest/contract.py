"""Ingestion adapter contract (design.md section 9.1).

Every source adapter (RunSignup now; Tavily discovery, Task 9) implements
:class:`SourceAdapter`. Adapters cannot write canonical tables -- they
return immutable discovery/fetch results and normalized staging inputs; a
caller drives them through
:class:`~xc_platform.db.repositories.staging.StagingRepository` (Task 4)
and, eventually, entity resolution (Task 10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class FrozenSeasonRefusedError(RuntimeError):
    """A fetch was requested for a season frozen by Requirement 1.8/4.9.

    2023, 2024, and 2025 are discoverable and previewable but never
    imported. Raised by :meth:`SourceAdapter.fetch`, never by
    :meth:`SourceAdapter.discover` -- discovery must still enumerate frozen
    seasons so the administrator can see what exists.
    """


@dataclass(frozen=True, slots=True)
class NormalizedUrl:
    """The result of parsing an administrator-submitted intake URL."""

    original_url: str
    source_namespace: str
    race_id: str
    requested_result_set_id: int | None
    requested_per_page: int | None


@dataclass(frozen=True, slots=True)
class DiscoveryRequest:
    normalized_url: NormalizedUrl
    correlation_id: str


@dataclass(frozen=True, slots=True)
class DiscoveredResultSet:
    """One result set found during discovery -- the unit :meth:`fetch` takes.

    ``source_namespace`` identifies which adapter/source this came from
    (e.g. ``"runsignup"``) as a plain string -- not a database-assigned
    ``data_sources.source_id``. Adapters never hold a database connection
    (design.md section 9.1: they cannot write canonical tables); resolving
    the namespace to its ``source_id`` is the caller's job, via
    :meth:`~xc_platform.db.repositories.staging.StagingRepository.get_or_create_source`.
    """

    source_namespace: str
    race_id: str
    event_id: int
    event_name: str
    set_id: int
    set_name: str
    season_year: int
    meet_number: int
    division: str | None
    gender_code: str | None
    distance_meters: int | None
    public_results: bool
    preliminary_results: bool
    frozen_season: bool


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    race_info: dict[str, Any]
    result_sets: tuple[DiscoveredResultSet, ...]

    @property
    def importable(self) -> tuple[DiscoveredResultSet, ...]:
        return tuple(rs for rs in self.result_sets if not rs.frozen_season)

    @property
    def frozen(self) -> tuple[DiscoveredResultSet, ...]:
        return tuple(rs for rs in self.result_sets if rs.frozen_season)


@dataclass(frozen=True, slots=True)
class FetchResult:
    item: DiscoveredResultSet
    raw_rows: tuple[dict[str, Any], ...]
    headers: dict[str, str]
    pages_fetched: int
    content_sha256: str
    retrieved_at: str
    request_id: str | None = None


@dataclass(frozen=True, slots=True)
class StagedResultInput:
    """One row ready for :meth:`StagingRepository.stage_result`."""

    source_result_id: str | None
    idempotency_key: str
    raw_fields: dict[str, Any]
    candidate_fields: dict[str, Any]
    validation_warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_valid(self) -> bool:
        return not self.validation_warnings


class SourceAdapter(Protocol):
    def can_handle(self, normalized_url: NormalizedUrl) -> bool: ...

    def discover(self, request: DiscoveryRequest) -> DiscoveryResult: ...

    def fetch(self, item: DiscoveredResultSet) -> FetchResult: ...

    def normalize(self, payload: FetchResult) -> list[StagedResultInput]: ...
