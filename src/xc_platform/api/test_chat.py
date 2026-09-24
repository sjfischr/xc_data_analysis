"""Ask the Data over SSE (Task 19.2) and its access control (Task 19.4)."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from xc_platform.agents._fake_model import TextTurn, ToolCallTurn
from xc_platform.api.agent_invoker import AgentSwitch, runtime_session_id
from xc_platform.api.conftest import login
from xc_platform.api.context import AppContext

BODY = {"session_id": "conversation-1", "prompt": "How many athletes are there?"}


def _events(response: Any) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def test_streams_the_agent_turn_as_server_sent_events(
    client: TestClient, ctx: AppContext, scripted_model_factory: Any
) -> None:
    scripted_model_factory.script = [
        ToolCallTurn("list_dimensions_tool", {}),
        TextTurn("There are **0** athletes."),
    ]
    login(client, agent_access=True)

    response = client.post("/api/v1/chat/messages", json=BODY)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response)
    assert events[0]["type"] == "status"
    assert any(
        e["type"] == "tool" and e.get("name") == "list_dimensions_tool" for e in events
    )
    assert events[-2] == {"type": "final", "markdown": "There are **0** athletes."}
    assert events[-1]["type"] == "complete"


def test_follow_up_turn_sees_the_previous_exchange(
    client: TestClient, ctx: AppContext, scripted_model_factory: Any
) -> None:
    login(client, agent_access=True)
    scripted_model_factory.script = [TextTurn("First answer.")]
    client.post("/api/v1/chat/messages", json=BODY)
    scripted_model_factory.script = [TextTurn("Second answer.")]
    client.post("/api/v1/chat/messages", json={**BODY, "prompt": "And then?"})

    second_turn_model = scripted_model_factory.created[-1]
    texts = [
        block["text"]
        for message in second_turn_model.calls[0]
        for block in message["content"]
        if "text" in block
    ]
    assert texts[:3] == [BODY["prompt"], "First answer.", "And then?"]


def test_viewer_without_agent_access_is_forbidden(client: TestClient) -> None:
    login(client, agent_access=False)
    response = client.post("/api/v1/chat/messages", json=BODY)
    assert response.status_code == 403
    assert client.get("/api/v1/chat/status").json() == {
        "enabled": True,
        "access": False,
        "available": False,
    }


def test_admin_always_has_agent_access(
    client: TestClient, scripted_model_factory: Any
) -> None:
    scripted_model_factory.script = [TextTurn("ok")]
    login(client, role="admin")
    assert client.post("/api/v1/chat/messages", json=BODY).status_code == 200


def test_global_switch_off_returns_503_for_everyone(
    client: TestClient, ctx: AppContext
) -> None:
    ctx.agent_switch = AgentSwitch(reader=lambda: "false")
    login(client, role="admin")
    response = client.post("/api/v1/chat/messages", json=BODY)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "agent_disabled"
    assert client.get("/api/v1/chat/status").json()["available"] is False


def test_switch_read_failure_keeps_the_feature_on() -> None:
    def broken() -> str:
        raise RuntimeError("ssm unavailable")

    assert AgentSwitch(reader=broken).enabled() is True


def test_chat_requires_the_csrf_header(client: TestClient) -> None:
    login(client, agent_access=True)
    del client.headers["X-CSRF-Token"]
    assert client.post("/api/v1/chat/messages", json=BODY).status_code == 403


def test_prompt_and_session_id_are_validated(client: TestClient) -> None:
    login(client, agent_access=True)
    too_long = {**BODY, "prompt": "x" * 2001}
    bad_session = {**BODY, "session_id": "../../etc"}
    assert client.post("/api/v1/chat/messages", json=too_long).status_code == 422
    assert client.post("/api/v1/chat/messages", json=bad_session).status_code == 422


def test_delete_memory_forgets_the_conversation(
    client: TestClient, scripted_model_factory: Any
) -> None:
    login(client, agent_access=True)
    scripted_model_factory.script = [TextTurn("First answer.")]
    client.post("/api/v1/chat/messages", json=BODY)

    response = client.delete("/api/v1/chat/memory")

    assert response.status_code == 200
    assert response.json() == {"deleted_events": 1}


def test_runtime_session_id_is_scoped_to_the_actor_and_long_enough() -> None:
    a = runtime_session_id("a" * 64, "conversation-1")
    b = runtime_session_id("b" * 64, "conversation-1")
    assert a != b
    assert len(a) >= 33
