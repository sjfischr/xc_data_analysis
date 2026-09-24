"""Tests for the feasibility harness (Task 3.1). Dry-run only: no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from xc_platform.cli import feasibility

SAMPLE_URL = "https://runsignup.com/Race/Results/154050#resultSetId-691534;perpage:100"


def test_normalize_sample_url() -> None:
    normalized = feasibility.normalize_runsignup_results_url(SAMPLE_URL)
    assert normalized["race_id"] == "154050"
    assert normalized["result_set_id"] == "691534"
    assert normalized["per_page"] == 100
    assert "results_per_page=100" in normalized["rest_url"]
    assert "individual_result_set_id=691534" in normalized["rest_url"]


def test_normalize_rejects_unknown_url() -> None:
    with pytest.raises(feasibility.HarnessError):
        feasibility.normalize_runsignup_results_url("https://example.com/nope")


def test_host_allowlist_blocks_other_domains_and_http() -> None:
    allowed = feasibility.DEFAULT_ALLOWED_DOMAINS
    assert feasibility.host_allowed("https://runsignup.com/Rest/race/1", allowed)
    assert feasibility.host_allowed("https://api.tavily.com/search", allowed)
    assert not feasibility.host_allowed("https://evil.example.com/x", allowed)
    assert not feasibility.host_allowed("http://runsignup.com/Rest", allowed)
    assert not feasibility.host_allowed("https://runsignup.com.evil.com/", allowed)


def test_budget_is_enforced(tmp_path: Path) -> None:
    config = feasibility.HarnessConfig(
        live=False,
        budget_requests=1,
        timeout_s=5.0,
        allowed_domains=feasibility.DEFAULT_ALLOWED_DOMAINS,
        output_root=tmp_path,
    )
    _, planned = feasibility.plan_runsignup_probe(SAMPLE_URL)
    assert len(planned) == 2
    with pytest.raises(feasibility.HarnessError, match="budget"):
        feasibility.execute(config, planned)


def test_dry_run_sends_nothing_and_writes_redacted_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dummy_key = "tvly-dummy-key-for-tests"
    monkeypatch.setenv("TAVILY_API_KEY", dummy_key)

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run must not open network connections")

    monkeypatch.setattr(feasibility.urllib.request, "urlopen", boom)

    exit_code = feasibility.main(
        ["--out-root", str(tmp_path), "probe-tavily", "--query", "NVJCYO results"]
    )
    assert exit_code == 0

    run_dirs = list(tmp_path.iterdir())
    assert len(run_dirs) == 1
    probe = json.loads((run_dirs[0] / "probe.json").read_text(encoding="utf-8"))
    assert probe["mode"] == "dry-run"
    assert probe["requests"][0]["status"] == "dry-run (no request sent)"

    # The key value must never appear in any output; the report must exist.
    for artifact in ("probe.json", "report.md"):
        content = (run_dirs[0] / artifact).read_text(encoding="utf-8")
        assert dummy_key not in content


def test_cli_dry_run_runsignup(tmp_path: Path) -> None:
    exit_code = feasibility.main(
        ["--out-root", str(tmp_path), "probe-runsignup", "--url", SAMPLE_URL]
    )
    assert exit_code == 0
    run_dir = next(tmp_path.iterdir())
    report = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "dry-run" in report
    assert "154050" in report
