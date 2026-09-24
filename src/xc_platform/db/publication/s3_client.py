"""S3 access, narrowed to exactly what publication needs.

:class:`S3Client` is the interface both the reader and writer depend on.
:class:`Boto3S3Client` is the production implementation. :class:`FakeS3Client`
is an in-memory implementation with the same conditional-write semantics
(including S3's own quirk of accepting ``If-None-Match: *`` even for a
versioned bucket's delete-marker case), used so the publication logic's
concurrency and failure-handling paths can be tested fast and
deterministically -- including via fault injection -- without a real network
call. Task 6.5's real-S3 suite runs the identical writer/reader code against
:class:`Boto3S3Client` on a disposable bucket to confirm the fake is faithful.
"""

from __future__ import annotations

import hashlib
import shutil
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from xc_platform.db.publication.errors import (
    ObjectNotFoundError,
    PreconditionFailedError,
)


@dataclass(frozen=True, slots=True)
class ObjectMeta:
    etag: str
    version_id: str | None
    size: int


class S3Client(Protocol):
    def put_object(
        self,
        key: str,
        body_path: Path,
        *,
        if_none_match: bool = False,
        if_match: str | None = None,
    ) -> ObjectMeta: ...

    def get_object(self, key: str, dest_path: Path) -> ObjectMeta: ...

    def head_object(self, key: str) -> ObjectMeta | None: ...

    def delete_object(self, key: str, *, if_match: str | None = None) -> None: ...


# --- production implementation --------------------------------------------


@dataclass
class Boto3S3Client:
    """Thin boto3 wrapper. Deliberately the only module that imports boto3."""

    bucket: str
    client: Any

    @classmethod
    def create(cls, bucket: str, *, region: str | None = None) -> Boto3S3Client:
        import boto3

        return cls(bucket=bucket, client=boto3.client("s3", region_name=region))

    def _error_code(self, exc: Any) -> str | None:
        response = getattr(exc, "response", None)
        if not isinstance(response, dict):
            return None
        error = response.get("Error")
        if not isinstance(error, dict):
            return None
        code = error.get("Code")
        return code if isinstance(code, str) else None

    def put_object(
        self,
        key: str,
        body_path: Path,
        *,
        if_none_match: bool = False,
        if_match: str | None = None,
    ) -> ObjectMeta:
        from botocore.exceptions import ClientError

        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if if_none_match:
            kwargs["IfNoneMatch"] = "*"
        if if_match is not None:
            kwargs["IfMatch"] = if_match

        size = body_path.stat().st_size
        try:
            with body_path.open("rb") as handle:
                response = self.client.put_object(Body=handle, **kwargs)
        except ClientError as exc:
            code = self._error_code(exc)
            if code in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise PreconditionFailedError(
                    f"put_object precondition failed for key {key!r} ({code})"
                ) from exc
            raise
        return ObjectMeta(
            etag=str(response["ETag"]).strip('"'),
            version_id=response.get("VersionId"),
            size=size,
        )

    def get_object(self, key: str, dest_path: Path) -> ObjectMeta:
        from botocore.exceptions import ClientError

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body = response["Body"]
            with dest_path.open("wb") as handle:
                shutil.copyfileobj(body, handle)
        except ClientError as exc:
            code = self._error_code(exc)
            if code in ("NoSuchKey", "404"):
                raise ObjectNotFoundError(f"object not found: {key!r}") from exc
            raise
        return ObjectMeta(
            etag=str(response["ETag"]).strip('"'),
            version_id=response.get("VersionId"),
            size=dest_path.stat().st_size,
        )

    def head_object(self, key: str) -> ObjectMeta | None:
        from botocore.exceptions import ClientError

        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            code = self._error_code(exc)
            if code in ("404", "NoSuchKey", "NotFound"):
                return None
            raise
        return ObjectMeta(
            etag=str(response["ETag"]).strip('"'),
            version_id=response.get("VersionId"),
            size=int(response["ContentLength"]),
        )

    def delete_object(self, key: str, *, if_match: str | None = None) -> None:
        from botocore.exceptions import ClientError

        kwargs: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if if_match is not None:
            kwargs["IfMatch"] = if_match
        try:
            self.client.delete_object(**kwargs)
        except ClientError as exc:
            code = self._error_code(exc)
            if code in ("PreconditionFailed", "ConditionalRequestConflict"):
                raise PreconditionFailedError(
                    f"delete_object precondition failed for key {key!r} ({code})"
                ) from exc
            raise


# --- fake, in-memory implementation for fast deterministic tests ----------


@dataclass
class _FakeObject:
    etag: str
    version_id: str
    content: bytes


@dataclass
class FakeS3Client:
    """In-memory S3 stand-in with real conditional-write semantics.

    ``before_put``/``before_delete`` are optional fault-injection hooks
    (called with the key, before the operation is applied) so tests can
    simulate a crash at a specific point in a multi-step protocol -- e.g.
    "the process dies after the snapshot upload but before the manifest
    compare-and-swap."
    """

    _objects: dict[str, _FakeObject] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    before_put: Callable[[str], None] | None = None
    before_delete: Callable[[str], None] | None = None

    def put_object(
        self,
        key: str,
        body_path: Path,
        *,
        if_none_match: bool = False,
        if_match: str | None = None,
    ) -> ObjectMeta:
        content = body_path.read_bytes()
        with self._lock:
            # The hook fires before the precondition check (not after), so
            # a test can inject a concurrent writer's change here and have
            # it actually affect whether this put's If-Match/If-None-Match
            # condition holds -- simulating a genuine race, not just a
            # crash after this put already won it.
            if self.before_put is not None:
                self.before_put(key)
            existing = self._objects.get(key)
            if if_none_match and existing is not None:
                raise PreconditionFailedError(
                    f"put_object If-None-Match failed: {key!r} already exists"
                )
            if if_match is not None and (existing is None or existing.etag != if_match):
                raise PreconditionFailedError(
                    f"put_object If-Match failed for {key!r}: expected "
                    f"{if_match!r}, found {existing.etag if existing else None!r}"
                )
            etag = hashlib.md5(content, usedforsecurity=False).hexdigest()
            version_id = uuid.uuid4().hex
            self._objects[key] = _FakeObject(
                etag=etag, version_id=version_id, content=content
            )
            return ObjectMeta(etag=etag, version_id=version_id, size=len(content))

    def get_object(self, key: str, dest_path: Path) -> ObjectMeta:
        with self._lock:
            obj = self._objects.get(key)
            if obj is None:
                raise ObjectNotFoundError(f"object not found: {key!r}")
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(obj.content)
            return ObjectMeta(
                etag=obj.etag, version_id=obj.version_id, size=len(obj.content)
            )

    def head_object(self, key: str) -> ObjectMeta | None:
        with self._lock:
            obj = self._objects.get(key)
            if obj is None:
                return None
            return ObjectMeta(
                etag=obj.etag, version_id=obj.version_id, size=len(obj.content)
            )

    def delete_object(self, key: str, *, if_match: str | None = None) -> None:
        with self._lock:
            existing = self._objects.get(key)
            if existing is None:
                return
            if if_match is not None and existing.etag != if_match:
                raise PreconditionFailedError(
                    f"delete_object If-Match failed for {key!r}: expected "
                    f"{if_match!r}, found {existing.etag!r}"
                )
            if self.before_delete is not None:
                self.before_delete(key)
            del self._objects[key]

    # --- test-only introspection -------------------------------------

    def object_keys(self) -> list[str]:
        with self._lock:
            return sorted(self._objects)
