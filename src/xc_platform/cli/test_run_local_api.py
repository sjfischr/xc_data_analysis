from __future__ import annotations

from pathlib import Path

from xc_platform.api.app import create_app
from xc_platform.cli.run_local_api import build_local_context


def test_build_local_context_produces_a_working_app_context(tmp_path: Path) -> None:
    ctx = build_local_context(tmp_path / "data")
    try:
        active = ctx.snapshot_reader.current()
        assert active.manifest.publication_id
        app = create_app(ctx)
        assert app is not None
    finally:
        ctx.writer_conn.close()
