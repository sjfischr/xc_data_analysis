from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi.testclient import TestClient

from xc_platform.agents._fake_model import TextTurn
from xc_platform.api.agent_invoker import AgentCoreRuntimeInvoker
from xc_platform.api.conftest import login
from xc_platform.api.context import AppContext

BODY = {
    "session_id": "conversation-1",
    "prompt": "How many athletes?",
}


def test_submit_poll_and_replay_completed_job(
    client: TestClient,
    ctx: AppContext,
    scripted_model_factory: Any,
) -> None:
    login(client, agent_access=True)
    scripted_model_factory.script = [TextTurn("The answer.")]
    job_id = str(uuid.uuid4())
    response = client.post("/api/v1/chat/jobs", json={**BODY, "job_id": job_id})
    assert response.status_code == 202
    for _ in range(100):
        response = client.get(f"/api/v1/chat/jobs/{job_id}")
        if response.json()["job"]["state"] in {"completed", "failed"}:
            break
        time.sleep(0.01)
    body = response.json()
    assert body["job"]["state"] == "completed"
    assert body["job"]["publication_id"]
    assert any(
        e == {"type": "final", "markdown": "The answer."} for e in body["events"]
    )
    assert (
        client.get(f"/api/v1/chat/jobs/{job_id}?after={body['next_cursor']}").json()[
            "events"
        ]
        == []
    )
    assert (
        client.get("/api/v1/chat/jobs?session_id=conversation-1").json()["jobs"][0][
            "job_id"
        ]
        == job_id
    )
    response = client.post("/api/v1/chat/jobs", json={**BODY, "job_id": job_id})
    assert response.status_code == 202
    assert len(scripted_model_factory.created) == 1


def test_jobs_require_access_and_csrf(client: TestClient) -> None:
    job_id = str(uuid.uuid4())
    assert (
        client.post("/api/v1/chat/jobs", json={**BODY, "job_id": job_id}).status_code
        == 401
    )
    login(client, agent_access=False)
    assert (
        client.post("/api/v1/chat/jobs", json={**BODY, "job_id": job_id}).status_code
        == 403
    )
    login(client, role="admin")
    del client.headers["X-CSRF-Token"]
    assert (
        client.post("/api/v1/chat/jobs", json={**BODY, "job_id": job_id}).status_code
        == 403
    )
    assert client.post(f"/api/v1/chat/jobs/{job_id}/cancel").status_code == 403


def test_cross_actor_job_reads_and_cancel_are_not_found(
    client: TestClient,
    ctx: AppContext,
) -> None:
    login(client, agent_access=True)
    job_id = str(uuid.uuid4())
    ctx.get_chat_jobs().submit("b" * 64, "conversation-1", job_id, "Private")
    assert client.get(f"/api/v1/chat/jobs/{job_id}").status_code == 404
    assert client.post(f"/api/v1/chat/jobs/{job_id}/cancel").status_code == 404
    assert client.get("/api/v1/chat/jobs?session_id=conversation-1").json() == {
        "jobs": []
    }


def test_background_job_validation(client: TestClient) -> None:
    login(client, role="admin")
    for body in (
        {**BODY, "job_id": "../invalid"},
        {**BODY, "job_id": str(uuid.uuid4()), "prompt": "x" * 2001},
        {**BODY, "job_id": str(uuid.uuid4()), "session_id": "../invalid"},
    ):
        assert client.post("/api/v1/chat/jobs", json=body).status_code == 422


def test_runtime_submission_is_a_short_ack_and_uses_job_lifetime(
    ctx: AppContext,
) -> None:
    import asyncio
    import json

    requests = []
    closed = []

    class Body:
        def iter_lines(self) -> list[bytes]:
            return [b'data: {"type":"job_accepted","job_id":"accepted"}']

        def close(self) -> None:
            closed.append(True)

    class Client:
        def invoke_agent_runtime(self, **kwargs: Any) -> dict[str, Any]:
            requests.append(kwargs)
            return {"response": Body(), "contentType": "text/event-stream"}

    job_id = str(uuid.uuid4())
    jobs = ctx.get_chat_jobs()
    jobs.submit("a" * 64, "conversation-1", job_id, "Question")
    invoker = AgentCoreRuntimeInvoker(
        runtime_arn="arn", region="us-east-1", client=Client()
    )
    asyncio.run(
        invoker.submit_job(
            actor_id="a" * 64,
            session_id="conversation-1",
            prompt="Question",
            job_id=job_id,
            jobs=jobs,
        )
    )
    payload = json.loads(requests[0]["payload"])
    assert payload["action"] == "start_job"
    assert requests[0]["runtimeSessionId"].endswith(job_id)
    assert payload["session_id"] == "conversation-1"
    assert closed == [True]


def test_uncertain_submission_remains_retriable_with_same_id(
    client: TestClient,
    ctx: AppContext,
) -> None:
    from xc_platform.api.agent_invoker import InProcessAgentInvoker

    class Unavailable(InProcessAgentInvoker):
        async def submit_job(self, **kwargs: Any) -> None:
            raise RuntimeError("runtime connection unavailable")

    login(client, role="admin")
    ctx.agent_invoker = Unavailable(snapshot_reader=ctx.snapshot_reader)
    job_id = str(uuid.uuid4())
    response = client.post("/api/v1/chat/jobs", json={**BODY, "job_id": job_id})
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "job_submission_uncertain"
    response = client.get(f"/api/v1/chat/jobs/{job_id}")
    assert response.json()["job"]["state"] == "pending"
    ctx.agent_invoker = None
    # The stored request is retained, not lost or assigned a new ID.
    response = client.post("/api/v1/chat/jobs", json={**BODY, "job_id": job_id})
    assert response.status_code == 202
