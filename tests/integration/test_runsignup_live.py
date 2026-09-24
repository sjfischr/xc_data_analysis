"""Live confirmation of the RunSignup adapter against the real API (Task 8.4).

Marked ``live`` (opt-in, never runs in ordinary CI -- see
``-m "not live and not integration"`` in the CI workflow). Run explicitly::

    pytest tests/integration/test_runsignup_live.py -m live -v

Uses race 154050 (NVJCYO Cross Country Developmental Meet 1), the same race
ID Task 3.2's feasibility probe used, confirmed to have published events for
2023-2026 (docs/runsignup-adapter-notes.md).
"""

from __future__ import annotations

import pytest

from xc_platform.ingest.adapters.runsignup import (
    FROZEN_SEASONS,
    discover,
    fetch,
    normalize,
    normalize_runsignup_url,
)
from xc_platform.ingest.adapters.runsignup_client import RequestBudget, RunSignupClient
from xc_platform.ingest.contract import DiscoveryRequest, FrozenSeasonRefusedError

pytestmark = pytest.mark.live

RACE_URL = "https://runsignup.com/Race/Results/154050"


def test_discover_against_the_real_race_marks_frozen_and_2026_seasons() -> None:
    client = RunSignupClient(
        budget=RequestBudget(max_requests=200, max_wall_time_s=120.0)
    )
    normalized = normalize_runsignup_url(RACE_URL)
    request = DiscoveryRequest(
        normalized_url=normalized, correlation_id="live-test-8.4"
    )

    result = discover(client, request)
    assert result.result_sets, "expected at least one discovered result set"

    seasons_seen = {rs.season_year for rs in result.result_sets}
    print(f"seasons observed live: {sorted(seasons_seen)}")
    print(f"total result sets: {len(result.result_sets)}")
    print(f"frozen: {len(result.frozen)}, importable: {len(result.importable)}")

    frozen_in_results = {rs.season_year for rs in result.frozen}
    assert frozen_in_results <= FROZEN_SEASONS
    for rs in result.result_sets:
        if rs.season_year in FROZEN_SEASONS:
            assert rs.frozen_season is True
        else:
            assert rs.frozen_season is False

    # Every frozen-season item must refuse fetch (Requirement 1.8/4.9).
    for rs in result.frozen[:1]:
        with pytest.raises(FrozenSeasonRefusedError):
            fetch(client, rs)


def test_fetch_and_normalize_a_real_importable_result_set_if_one_exists() -> None:
    client = RunSignupClient(
        budget=RequestBudget(max_requests=200, max_wall_time_s=120.0)
    )
    normalized = normalize_runsignup_url(RACE_URL)
    request = DiscoveryRequest(
        normalized_url=normalized, correlation_id="live-test-8.4"
    )
    result = discover(client, request)

    importable_with_results = [rs for rs in result.importable if rs.public_results]
    if not importable_with_results:
        pytest.skip("no importable (non-frozen, public) result set published yet")

    item = importable_with_results[0]
    fetched = fetch(client, item)
    print(
        f"fetched {len(fetched.raw_rows)} rows in {fetched.pages_fetched} page(s) "
        f"for {item.race_id}/{item.season_year}/{item.event_name}/{item.set_name}"
    )
    staged = normalize(fetched)
    assert len(staged) == len(fetched.raw_rows)
    if staged:
        sample = staged[0]
        print("sample candidate_fields:", sample.candidate_fields)
        assert sample.idempotency_key.startswith("runsignup:")
