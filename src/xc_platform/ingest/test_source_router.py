from __future__ import annotations

import pytest

from xc_platform.ingest.adapters.runsignup import RunSignupAdapter
from xc_platform.ingest.adapters.runsignup_client import RequestBudget, RunSignupClient
from xc_platform.ingest.source_router import (
    UnsupportedSourceError,
    normalize_intake_url,
    select_adapter,
)


def test_normalize_intake_url_accepts_a_runsignup_url() -> None:
    normalized = normalize_intake_url("https://runsignup.com/Race/Results/154050")
    assert normalized.race_id == "154050"


def test_normalize_intake_url_accepts_a_mirror_host() -> None:
    normalized = normalize_intake_url("https://trisignup.com/Race/Results/154708")
    assert normalized.race_id == "154708"


def test_normalize_intake_url_rejects_a_non_runsignup_host() -> None:
    with pytest.raises(UnsupportedSourceError):
        normalize_intake_url("https://evil.example.com/Race/Results/154050")


def test_normalize_intake_url_rejects_a_non_https_url() -> None:
    with pytest.raises(UnsupportedSourceError):
        normalize_intake_url("http://runsignup.com/Race/Results/154050")


def test_select_adapter_returns_the_runsignup_adapter() -> None:
    normalized = normalize_intake_url("https://runsignup.com/Race/Results/154050")
    client = RunSignupClient(
        budget=RequestBudget(max_requests=10, max_wall_time_s=10.0)
    )
    adapter = select_adapter(normalized, client)
    assert isinstance(adapter, RunSignupAdapter)
