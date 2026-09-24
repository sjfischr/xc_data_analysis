from __future__ import annotations

from pathlib import Path

import pytest

from xc_platform.db.publication.errors import (
    ObjectNotFoundError,
    PreconditionFailedError,
)
from xc_platform.db.publication.s3_client import FakeS3Client


def _write(path: Path, content: bytes) -> Path:
    path.write_bytes(content)
    return path


def test_put_get_round_trip(tmp_path: Path) -> None:
    s3 = FakeS3Client()
    body = _write(tmp_path / "body.bin", b"hello world")
    meta = s3.put_object("k1", body)
    assert meta.size == 11

    dest = tmp_path / "out.bin"
    got = s3.get_object("k1", dest)
    assert dest.read_bytes() == b"hello world"
    assert got.etag == meta.etag


def test_head_object_returns_none_when_missing(tmp_path: Path) -> None:
    s3 = FakeS3Client()
    assert s3.head_object("does-not-exist") is None


def test_get_object_raises_when_missing(tmp_path: Path) -> None:
    s3 = FakeS3Client()
    with pytest.raises(ObjectNotFoundError):
        s3.get_object("does-not-exist", tmp_path / "out.bin")


def test_if_none_match_rejects_existing_key(tmp_path: Path) -> None:
    s3 = FakeS3Client()
    body = _write(tmp_path / "body.bin", b"v1")
    s3.put_object("k1", body, if_none_match=True)
    with pytest.raises(PreconditionFailedError):
        s3.put_object("k1", body, if_none_match=True)


def test_if_match_requires_current_etag(tmp_path: Path) -> None:
    s3 = FakeS3Client()
    body_v1 = _write(tmp_path / "v1.bin", b"v1")
    meta_v1 = s3.put_object("k1", body_v1)

    body_v2 = _write(tmp_path / "v2.bin", b"v2")
    with pytest.raises(PreconditionFailedError):
        s3.put_object("k1", body_v2, if_match="stale-etag")

    # The real ETag succeeds.
    meta_v2 = s3.put_object("k1", body_v2, if_match=meta_v1.etag)
    assert meta_v2.etag != meta_v1.etag


def test_delete_object_if_match(tmp_path: Path) -> None:
    s3 = FakeS3Client()
    body = _write(tmp_path / "body.bin", b"v1")
    meta = s3.put_object("k1", body)

    with pytest.raises(PreconditionFailedError):
        s3.delete_object("k1", if_match="stale-etag")
    assert s3.head_object("k1") is not None

    s3.delete_object("k1", if_match=meta.etag)
    assert s3.head_object("k1") is None


def test_delete_object_missing_key_is_a_no_op(tmp_path: Path) -> None:
    s3 = FakeS3Client()
    s3.delete_object("does-not-exist")  # must not raise


def test_before_put_fault_injection(tmp_path: Path) -> None:
    calls: list[str] = []

    def boom(key: str) -> None:
        calls.append(key)
        raise RuntimeError("simulated crash before this put lands")

    s3 = FakeS3Client(before_put=boom)
    body = _write(tmp_path / "body.bin", b"v1")
    with pytest.raises(RuntimeError, match="simulated crash"):
        s3.put_object("k1", body)
    assert calls == ["k1"]
    assert s3.head_object("k1") is None  # the put never actually landed
