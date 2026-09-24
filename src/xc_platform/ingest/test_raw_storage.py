from __future__ import annotations

from pathlib import Path

from xc_platform.db.publication.s3_client import FakeS3Client
from xc_platform.ingest.contract import DiscoveredResultSet, FetchResult
from xc_platform.ingest.raw_storage import RawObjectStore


def _item() -> DiscoveredResultSet:
    return DiscoveredResultSet(
        source_namespace="runsignup",
        race_id="154050",
        event_id=1,
        event_name="Meet 1",
        set_id=101,
        set_name="Varsity Girls",
        season_year=2026,
        meet_number=1,
        division="Varsity",
        gender_code="F",
        distance_meters=5000,
        public_results=True,
        preliminary_results=False,
        frozen_season=False,
    )


def _fetch_result(item: DiscoveredResultSet) -> FetchResult:
    import hashlib
    import json

    rows = ({"place": "1", "first_name": "Jane", "last_name": "Doe"},)
    content = json.dumps(list(rows), sort_keys=True, default=str).encode("utf-8")
    return FetchResult(
        item=item,
        raw_rows=rows,
        headers={},
        pages_fetched=1,
        content_sha256=hashlib.sha256(content).hexdigest(),
        retrieved_at="2026-09-22T00:00:00Z",
    )


def test_store_fetch_result_writes_to_s3_and_returns_matching_digest(
    tmp_path: Path,
) -> None:
    s3 = FakeS3Client()
    store = RawObjectStore(s3, work_dir=tmp_path / "work")
    item = _item()
    fetch_result = _fetch_result(item)

    stored = store.store_fetch_result(
        source_namespace="runsignup", item=item, fetch_result=fetch_result
    )

    assert stored.content_sha256 == fetch_result.content_sha256
    assert stored.raw_s3_key.startswith("raw/runsignup/2026/154050-1-101-")
    meta = s3.head_object(stored.raw_s3_key)
    assert meta is not None
    assert meta.size == stored.byte_size


def test_store_fetch_result_cleans_up_its_temp_file(tmp_path: Path) -> None:
    s3 = FakeS3Client()
    work_dir = tmp_path / "work"
    store = RawObjectStore(s3, work_dir=work_dir)
    item = _item()
    store.store_fetch_result(
        source_namespace="runsignup", item=item, fetch_result=_fetch_result(item)
    )
    assert list(work_dir.glob("*.tmp")) == []
