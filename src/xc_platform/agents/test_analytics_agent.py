"""End-to-end proof that the analytics agent's tool-calling loop actually
works: a scripted model calls a real tool against a real (test) SQLite
database and the agent's final answer reflects the tool's real result.
Uses :class:`~xc_platform.agents._fake_model.FakeModel` -- no network call,
no AWS credential, so this runs in ordinary CI.
"""

from __future__ import annotations

import sqlite3

from xc_platform.agents._fake_model import FakeModel, TextTurn, ToolCallTurn
from xc_platform.agents.analytics_agent import (
    build_analytics_agent,
    build_bedrock_model,
    thinking_request_fields,
)
from xc_platform.db.repositories.canonical import CanonicalReadRepository
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository
from xc_platform.migration.canonical_writer import HistoricalCanonicalWriter


def test_agent_calls_list_dimensions_tool_and_grounds_its_answer(
    conn: sqlite3.Connection,
) -> None:
    write = CanonicalWriteRepository(conn)
    historical = HistoricalCanonicalWriter(conn)
    write.create_school(canonical_name="St Agnes")
    write.create_athlete(display_name="Jane Doe")
    meet_id = historical.get_or_create_meet(
        season_year=2026, meet_number=1, name="Meet 1", series="NVJCYO"
    )
    historical.get_or_create_race(
        meet_id=meet_id, division_code="Varsity", gender_code="F", distance_meters=5000
    )

    canonical = CanonicalReadRepository(conn)
    fake_model = FakeModel(
        script=[
            ToolCallTurn(tool_name="list_dimensions_tool", tool_input={}),
            TextTurn(
                text=(
                    "The dataset covers season 2026 with 1 school and 1 "
                    "athlete (publication pub-test-1)."
                )
            ),
        ]
    )
    agent = build_analytics_agent(
        canonical, publication_id="pub-test-1", model=fake_model
    )

    result = agent("What seasons are covered?")

    assert "2026" in str(result)
    assert "pub-test-1" in str(result)
    # The fake model was actually driven twice: once to decide to call the
    # tool, once more after seeing the real tool result.
    assert len(fake_model.calls) == 2
    # The second call's message history contains the real tool result, not
    # a stub -- proving the tool executed against the real database.
    second_call_text = str(fake_model.calls[1])
    assert "St Agnes" not in second_call_text  # list_dimensions doesn't leak names
    assert "2026" in second_call_text
    assert "pub-test-1" in second_call_text


def test_agent_answers_directly_without_a_tool_call_when_unneeded(
    conn: sqlite3.Connection,
) -> None:
    canonical = CanonicalReadRepository(conn)
    fake_model = FakeModel(script=[TextTurn(text="Hello! Ask me about race results.")])
    agent = build_analytics_agent(
        canonical, publication_id="pub-test-1", model=fake_model
    )

    result = agent("hi")

    assert "Hello" in str(result)
    assert len(fake_model.calls) == 1


def test_thinking_fields_follow_the_model_family() -> None:
    assert thinking_request_fields("us.anthropic.claude-sonnet-4-6", effort="low") == {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "low"},
    }
    assert thinking_request_fields("us.anthropic.claude-haiku-4-5-20251001-v1:0") == {
        "thinking": {"type": "enabled", "budget_tokens": 4096}
    }
    assert (
        thinking_request_fields(
            "us.anthropic.claude-haiku-4-5-20251001-v1:0", thinking=False
        )
        == {}
    )
    # Sonnet 5.5 rejects "disabled"; Opus 5.5 can't turn thinking off at all.
    assert thinking_request_fields("us.anthropic.claude-sonnet-5-5", thinking=False)[
        "thinking"
    ] == {"type": "between_tools"}
    assert thinking_request_fields("us.anthropic.claude-opus-5-5", thinking=False) == {
        "output_config": {"effort": "low"}
    }
    assert thinking_request_fields("us.anthropic.claude-3-haiku-20240307-v1:0") == {}


def test_temperature_is_only_sent_without_thinking() -> None:
    with_thinking = build_bedrock_model("us.anthropic.claude-sonnet-4-6", thinking=True)
    assert "temperature" not in with_thinking.config
    assert with_thinking.config["additional_request_fields"]["thinking"] == {
        "type": "adaptive"
    }
    without = build_bedrock_model("us.anthropic.claude-sonnet-4-6", thinking=False)
    assert without.config["temperature"] == 0
    assert "additional_request_fields" not in without.config
