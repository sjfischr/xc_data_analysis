"""Regression test for the schema/migrations packaging bug found live
during Task 18.2 (2026-09-23): see build_production_context's comment in
run_production_api.py for the full story.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from xc_platform.cli import run_production_api


def test_build_production_context_fails_fast_when_no_migrations_are_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XC_SNAPSHOT_BUCKET", "test-bucket")
    monkeypatch.setattr(run_production_api, "latest_schema_version", lambda: 0)

    with pytest.raises(RuntimeError, match="migrations/ directory"):
        run_production_api.build_production_context(data_dir=tmp_path)
