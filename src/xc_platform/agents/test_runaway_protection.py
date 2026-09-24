"""Proves Requirement 15.3's per-request runaway protection actually stops
an agent that would otherwise call a tool forever (Task 11.3/11.7's
tool-limit evaluation).
"""

from __future__ import annotations

import sqlite3

from xc_platform.agents._fake_model import FakeModel, ToolCallTurn
from xc_platform.agents.analytics_agent import (
    DEFAULT_LIMITS,
    build_analytics_agent,
    run_turn,
)
from xc_platform.db.repositories.canonical import CanonicalReadRepository


def test_run_turn_stops_a_model_that_never_stops_calling_tools(
    conn: sqlite3.Connection,
) -> None:
    canonical = CanonicalReadRepository(conn)
    # A model scripted to call a real tool every single turn, forever --
    # exactly the "malfunctioning agent looping without bound" Requirement
    # 15.3 exists to stop.
    fake_model = FakeModel(
        script=[ToolCallTurn(tool_name="list_dimensions_tool", tool_input={})],
        loop=True,
    )
    agent = build_analytics_agent(canonical, publication_id="pub-1", model=fake_model)

    result = run_turn(agent, "loop forever please")

    assert result.stop_reason == "limit_turns"
    assert len(fake_model.calls) == DEFAULT_LIMITS["turns"]


def test_run_turn_lets_a_caller_override_the_default_limits(
    conn: sqlite3.Connection,
) -> None:
    canonical = CanonicalReadRepository(conn)
    fake_model = FakeModel(
        script=[ToolCallTurn(tool_name="list_dimensions_tool", tool_input={})],
        loop=True,
    )
    agent = build_analytics_agent(canonical, publication_id="pub-1", model=fake_model)

    result = run_turn(agent, "loop forever please", limits={"turns": 2})

    assert result.stop_reason == "limit_turns"
    assert len(fake_model.calls) == 2
