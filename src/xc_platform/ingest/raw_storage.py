"""Raw fetched-payload storage (Task 12.2, design.md section 7's S3
durability pattern applied to source objects, not just published
snapshots).

Reuses the exact same :class:`~xc_platform.db.publication.s3_client.S3Client`
Protocol/``Boto3S3Client``/``FakeS3Client`` Task 6 already built and proved
against a real disposable bucket -- a second, parallel S3 client
implementation for raw objects would just be the same conditional-write
logic copied, with more surface to keep in sync.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from xc_platform.db.publication.s3_client import S3Client
from xc_platform.ingest.contract import DiscoveredResultSet, FetchResult


@dataclass(frozen=True, slots=True)
class StoredRawObject:
    raw_s3_key: str
    content_sha256: str
    byte_size: int
    media_type: str


class RawObjectStore:
    def __init__(
        self, s3: S3Client, *, work_dir: Path, key_prefix: str = "raw"
    ) -> None:
        self._s3 = s3
        self._work_dir = work_dir
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._key_prefix = key_prefix

    def store_fetch_result(
        self,
        *,
        source_namespace: str,
        item: DiscoveredResultSet,
        fetch_result: FetchResult,
    ) -> StoredRawObject:
        """Store exactly the rows the adapter already hashed as
        ``fetch_result.content_sha256`` -- reusing that digest (rather than
        hashing our own wrapped representation) keeps the raw object's
        identity tied to the content an unchanged re-fetch would reproduce
        byte-for-byte, which is what makes Requirement 4.6's "unchanged
        re-import creates no duplicate" check work.
        """
        payload = json.dumps(
            list(fetch_result.raw_rows), sort_keys=True, default=str
        ).encode("utf-8")
        key = (
            f"{self._key_prefix}/{source_namespace}/{item.season_year}/"
            f"{item.race_id}-{item.event_id}-{item.set_id}-"
            f"{fetch_result.content_sha256}.json"
        )
        tmp_path = self._work_dir / f".{uuid.uuid4().hex}.tmp"
        tmp_path.write_bytes(payload)
        try:
            self._s3.put_object(key, tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
        return StoredRawObject(
            raw_s3_key=key,
            content_sha256=fetch_result.content_sha256,
            byte_size=len(payload),
            media_type="application/json",
        )
