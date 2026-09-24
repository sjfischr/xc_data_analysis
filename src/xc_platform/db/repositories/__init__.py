"""Typed repositories: the only approved write surface onto SQLite.

Requirement 10.3 rejects agent- or adapter-generated database writes except
through separately authorized, validated workflow tools. Concretely for this
package: ingestion adapters, resolution agents, and analytics tools import
the repository classes below and call their typed methods -- they never
import :mod:`xc_platform.db.connection` or hold a raw ``sqlite3.Connection``
of their own. The repositories are where transaction boundaries, constraint
handling, and rollback-on-failure (Requirement 2.7) live.

Each repository wraps one connection and one area of the schema:

* :class:`~xc_platform.db.repositories.canonical.CanonicalReadRepository` --
  read-only lookups over meets, races, schools, athletes, and results.
* :class:`~xc_platform.db.repositories.staging.StagingRepository` -- source
  registration, raw-object records, and staged results (pre-canonical).
* :class:`~xc_platform.db.repositories.resolution.ResolutionRepository` --
  resolution cases and decisions (Requirement 8).
* :class:`~xc_platform.db.repositories.ingest.IngestRunRepository` -- ingest
  run lifecycle state and counts (Requirement 7.7).
* :class:`~xc_platform.db.repositories.publications.PublicationRepository` --
  publication lineage metadata mirrored inside the database itself.
"""

from __future__ import annotations

from xc_platform.db.repositories.base import BaseRepository
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.ingest import IngestRunRepository
from xc_platform.db.repositories.publications import PublicationRepository
from xc_platform.db.repositories.resolution import ResolutionRepository
from xc_platform.db.repositories.staging import StagingRepository

__all__ = [
    "BaseRepository",
    "CanonicalReadRepository",
    "IngestRunRepository",
    "PublicationRepository",
    "ResolutionRepository",
    "StagingRepository",
]
