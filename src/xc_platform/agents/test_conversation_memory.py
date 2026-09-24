from __future__ import annotations

from typing import Any

from xc_platform.agents.conversation_memory import (
    MAX_TURNS,
    AgentCoreConversationStore,
    InMemoryConversationStore,
)

A = "a" * 64
B = "b" * 64


def test_in_memory_store_is_isolated_by_actor_and_session() -> None:
    store = InMemoryConversationStore()
    store.append(A, "s1", "q", "answer")
    assert store.load(A, "s1") == [
        {"role": "user", "content": [{"text": "q"}]},
        {"role": "assistant", "content": [{"text": "answer"}]},
    ]
    assert store.load(B, "s1") == []
    assert store.load(A, "s2") == []


def test_in_memory_store_keeps_only_recent_turns() -> None:
    store = InMemoryConversationStore()
    for i in range(MAX_TURNS + 3):
        store.append(A, "s1", f"q{i}", f"a{i}")
    messages = store.load(A, "s1")
    assert len(messages) == MAX_TURNS * 2
    assert messages[0]["content"][0]["text"] == "q3"


class _FakeDataPlane:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def create_event(self, **kwargs: Any) -> dict[str, Any]:
        event = {**kwargs, "eventId": f"e{len(self.events)}"}
        self.events.append(event)
        return {"event": event}

    def list_events(
        self,
        *,
        memoryId: str,  # noqa: N803 -- boto3's parameter names
        actorId: str,  # noqa: N803
        sessionId: str,  # noqa: N803
        **_: Any,
    ) -> dict[str, Any]:
        return {
            "events": [
                e
                for e in self.events
                if e["actorId"] == actorId and e["sessionId"] == sessionId
            ]
        }

    def list_sessions(self, *, memoryId: str, actorId: str, **_: Any) -> dict[str, Any]:  # noqa: N803
        ids = dict.fromkeys(
            e["sessionId"] for e in self.events if e["actorId"] == actorId
        )
        return {"sessionSummaries": [{"sessionId": s} for s in ids]}

    def delete_event(self, *, eventId: str, **_: Any) -> None:  # noqa: N803
        self.events = [e for e in self.events if e["eventId"] != eventId]


def test_agentcore_store_round_trips_turns_and_deletes_one_actor() -> None:
    plane = _FakeDataPlane()
    store = AgentCoreConversationStore(memory_id="m", region="us-east-1", client=plane)
    store.append(A, "s1", "q1", "a1")
    store.append(A, "s2", "q2", "a2")
    store.append(B, "s1", "q3", "a3")

    assert [m["content"][0]["text"] for m in store.load(A, "s1")] == ["q1", "a1"]
    assert store.delete_actor(A) == 2
    assert store.load(A, "s1") == []
    assert [m["content"][0]["text"] for m in store.load(B, "s1")] == ["q3", "a3"]
