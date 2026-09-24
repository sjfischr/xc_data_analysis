"""The streamed chat contract (Task 19.2), driven by the scripted model."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from xc_platform.agents._fake_model import FakeModel, TextTurn, ToolCallTurn
from xc_platform.agents.chat_service import run_chat_turn
from xc_platform.agents.chat_stream import answer_text, stream_turn
from xc_platform.agents.conversation_memory import InMemoryConversationStore
from xc_platform.db.connection import open_writer_connection
from xc_platform.db.migrator import migrate
from xc_platform.db.publication.reader import SnapshotReader
from xc_platform.db.publication.s3_client import FakeS3Client
from xc_platform.db.publication.writer import SnapshotPublisher
from xc_platform.db.repositories.canonical_write import CanonicalWriteRepository


def _reader(tmp_path: Path) -> SnapshotReader:
    db_path = tmp_path / "source.db"
    conn = open_writer_connection(db_path)
    migrate(conn)
    CanonicalWriteRepository(conn).create_athlete(display_name="Audrey Walker")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    s3 = FakeS3Client()
    SnapshotPublisher(s3, work_dir=tmp_path / "w", owner_id="t").publish(
        db_path, created_by="t", ingest_run_id="t", summary={}
    )
    return SnapshotReader(s3, cache_dir=tmp_path / "c")


def _collect(gen: Any) -> list[dict[str, Any]]:
    async def run() -> list[dict[str, Any]]:
        return [e async for e in gen]

    return asyncio.run(run())


SCRIPT: list[ToolCallTurn | TextTurn] = [
    ToolCallTurn("find_athletes_tool", {"query": "Walker"}),
    ToolCallTurn(
        "build_chart_tool",
        {
            "chart_type": "bar",
            "title": "Demo",
            "data": [{"athlete": "Audrey Walker", "races": 1}],
            "x": "athlete",
            "y": "races",
        },
    ),
    ToolCallTurn("suggest_follow_ups_tool", {"questions": ["What about 2024?"]}),
    TextTurn("**Audrey Walker** is in the data."),
]


def test_stream_emits_the_documented_event_sequence(tmp_path: Path) -> None:
    events = _collect(
        stream_turn(
            snapshot_reader=_reader(tmp_path),
            prompt="Who is Walker?",
            model=FakeModel(SCRIPT),
        )
    )
    types = [e["type"] for e in events]

    assert types[0] == "status"
    assert types[-2:] == ["final", "complete"]
    assert "chart" in types
    assert "follow_ups" in types
    tool_events = [e for e in events if e["type"] == "tool"]
    assert tool_events[0]["name"] == "find_athletes_tool"
    assert tool_events[0]["label"] == "Looking up athletes"
    assert tool_events[0]["status"] == "running"
    assert "done" in {e["status"] for e in tool_events}
    chart = next(e for e in events if e["type"] == "chart")
    assert chart["spec"]["data"]["values"][0]["athlete"] == "Audrey Walker"
    assert events[-2]["markdown"] == "**Audrey Walker** is in the data."
    assert events[-1]["publication_id"] == events[0]["publication_id"]


def test_chat_turn_stores_the_answer_for_the_next_turn(tmp_path: Path) -> None:
    store = InMemoryConversationStore()
    actor = "a" * 64
    _collect(
        run_chat_turn(
            snapshot_reader=_reader(tmp_path),
            store=store,
            actor_id=actor,
            session_id="session-1",
            prompt="Who is Walker?",
            model=FakeModel([TextTurn("An athlete.")]),
        )
    )
    assert [m["content"][0]["text"] for m in store.load(actor, "session-1")] == [
        "Who is Walker?",
        "An athlete.",
    ]


def test_answer_text_keeps_text_beside_presentation_tools_only() -> None:
    messages: Any = [
        {
            "role": "assistant",
            "content": [
                {"text": "Let me look."},
                {"toolUse": {"name": "find_athletes_tool"}},
            ],
        },
        {"role": "user", "content": [{"toolResult": {}}]},
        {
            "role": "assistant",
            "content": [
                {"text": "The answer."},
                {"toolUse": {"name": "suggest_follow_ups_tool"}},
            ],
        },
        {"role": "user", "content": [{"toolResult": {}}]},
        {"role": "assistant", "content": []},
    ]
    assert answer_text(messages) == "The answer."


def test_answer_text_strips_narration_beside_a_chart_call() -> None:
    messages: Any = [
        {
            "role": "assistant",
            "content": [
                {
                    "text": "Perfect! Now I'll create a chart of team wins:\n\n"
                    "**St Agnes** won 11."
                },
                {"toolUse": {"name": "build_chart_tool"}},
            ],
        },
        {
            "role": "assistant",
            "content": [
                {
                    "text": "Perfect score of 15 for St James, "
                    "and I'll note the fit is weak."
                }
            ],
        },
    ]
    assert answer_text(messages) == (
        "**St Agnes** won 11.\n\n"
        "Perfect score of 15 for St James, and I'll note the fit is weak."
    )
