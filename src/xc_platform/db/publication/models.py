"""Manifest and writer-lease domain models, validated at construction (Task 6.1).

Requirements: R3.3-R3.9.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from xc_platform.db.migrator import latest_schema_version
from xc_platform.db.publication import layout
from xc_platform.db.publication.errors import InvalidManifestError

_SHA256_HEX_LENGTH = 64


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise InvalidManifestError(f"manifest field {key!r} must be a non-empty string")
    return value


def _require_optional_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise InvalidManifestError(
            f"manifest field {key!r} must be a non-empty string or null"
        )
    return value


def _require_positive_int(data: dict[str, Any], key: str) -> int:
    value = data.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise InvalidManifestError(f"manifest field {key!r} must be a positive integer")
    return value


def _require_timestamp(data: dict[str, Any], key: str) -> str:
    value = _require_str(data, key)
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise InvalidManifestError(
            f"manifest field {key!r}={value!r} is not a valid ISO-8601 timestamp"
        ) from exc
    return value


def _require_summary(data: dict[str, Any]) -> dict[str, int]:
    value = data.get("summary")
    if not isinstance(value, dict):
        raise InvalidManifestError("manifest field 'summary' must be an object")
    summary: dict[str, int] = {}
    for k, v in value.items():
        if (
            not isinstance(k, str)
            or not isinstance(v, int)
            or isinstance(v, bool)
            or v < 0
        ):
            raise InvalidManifestError(
                f"manifest field 'summary' entry {k!r}={v!r} must be a "
                "non-negative integer count"
            )
        summary[k] = v
    return summary


@dataclass(frozen=True, slots=True)
class Manifest:
    """``database/active.json`` (design.md section 7.1)."""

    publication_id: str
    parent_publication_id: str | None
    snapshot_key: str
    snapshot_version_id: str
    sha256: str
    byte_size: int
    schema_version: int
    created_at: str
    created_by: str
    summary: dict[str, int]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        """Parse and fully validate a manifest document.

        Raises :class:`~xc_platform.db.publication.errors.InvalidManifestError`
        for a malformed document, an unsupported schema version, or a
        ``snapshot_key`` that does not match the ``database/snapshots/<uuid>
        /xc.db`` layout -- the path-traversal defense (Task 6.1): the key is
        never trusted as free-form input. It does not have to reference this
        same manifest's own ``publication_id``; a restore manifest
        legitimately references an older publication's snapshot (design.md
        section 7.4).
        """
        publication_id = _require_str(data, "publication_id")
        if not layout.is_valid_publication_id(publication_id):
            raise InvalidManifestError(
                f"manifest publication_id {publication_id!r} is not a valid UUID4"
            )

        parent_publication_id = _require_optional_str(data, "parent_publication_id")
        if parent_publication_id is not None and not layout.is_valid_publication_id(
            parent_publication_id
        ):
            raise InvalidManifestError(
                f"manifest parent_publication_id {parent_publication_id!r} "
                "is not a valid UUID4"
            )

        snapshot_key = _require_str(data, "snapshot_key")
        if layout.snapshot_publication_id_from_key(snapshot_key) is None:
            raise InvalidManifestError(
                f"manifest snapshot_key {snapshot_key!r} does not match the "
                "required database/snapshots/<uuid>/xc.db layout; refused "
                "rather than trusted as a free-form path"
            )

        sha256_hex = _require_str(data, "sha256")
        if len(sha256_hex) != _SHA256_HEX_LENGTH or any(
            c not in "0123456789abcdef" for c in sha256_hex.lower()
        ):
            raise InvalidManifestError(
                f"manifest sha256 {sha256_hex!r} is not 64 hex chars"
            )

        schema_version = _require_positive_int(data, "schema_version")
        supported = latest_schema_version()
        if schema_version > supported:
            raise InvalidManifestError(
                f"manifest schema_version {schema_version} is newer than the "
                f"highest version this code supports ({supported}); refuse "
                "rather than open with an unknown schema"
            )

        return cls(
            publication_id=publication_id,
            parent_publication_id=parent_publication_id,
            snapshot_key=snapshot_key,
            snapshot_version_id=_require_str(data, "snapshot_version_id"),
            sha256=sha256_hex.lower(),
            byte_size=_require_positive_int(data, "byte_size"),
            schema_version=schema_version,
            created_at=_require_timestamp(data, "created_at"),
            created_by=_require_str(data, "created_by"),
            summary=_require_summary(data),
        )

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InvalidManifestError(f"manifest is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise InvalidManifestError("manifest JSON must be an object")
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "publication_id": self.publication_id,
            "parent_publication_id": self.parent_publication_id,
            "snapshot_key": self.snapshot_key,
            "snapshot_version_id": self.snapshot_version_id,
            "sha256": self.sha256,
            "byte_size": self.byte_size,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "summary": self.summary,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n"


@dataclass(frozen=True, slots=True)
class WriterLease:
    """``database/locks/writer.json`` (design.md section 7.3)."""

    owner_id: str
    ingest_run_id: str
    acquired_at: str
    expires_at: str
    heartbeat_at: str

    @classmethod
    def new(
        cls, *, owner_id: str, ingest_run_id: str, ttl_seconds: float
    ) -> WriterLease:
        now = datetime.now(UTC)
        expires = now + timedelta(seconds=ttl_seconds)

        def iso(dt: datetime) -> str:
            return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")

        return cls(
            owner_id=owner_id,
            ingest_run_id=ingest_run_id,
            acquired_at=iso(now),
            expires_at=iso(expires),
            heartbeat_at=iso(now),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WriterLease:
        return cls(
            owner_id=_require_str(data, "owner_id"),
            ingest_run_id=_require_str(data, "ingest_run_id"),
            acquired_at=_require_timestamp(data, "acquired_at"),
            expires_at=_require_timestamp(data, "expires_at"),
            heartbeat_at=_require_timestamp(data, "heartbeat_at"),
        )

    @classmethod
    def from_json(cls, text: str) -> WriterLease:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise InvalidManifestError(
                f"writer lease is not valid JSON: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise InvalidManifestError("writer lease JSON must be an object")
        return cls.from_dict(data)

    def to_dict(self) -> dict[str, Any]:
        return {
            "owner_id": self.owner_id,
            "ingest_run_id": self.ingest_run_id,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
            "heartbeat_at": self.heartbeat_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=False) + "\n"

    def is_expired(self, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        expires = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        return current >= expires
