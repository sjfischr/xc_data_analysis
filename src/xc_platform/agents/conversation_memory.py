"""Conversation history for the analytics agent (Task 19.2, design.md
12.4's short-term memory).

A turn is stored as text only -- the user's question and the agent's final
answer -- never tool inputs/outputs, chart data, or code. That is enough for
follow-ups ("and how did she do in 2024?") while keeping memory small and
free of raw query results. Every read and write is keyed by the
pseudonymous ``actor_id`` (an HMAC of the Cognito subject, never the raw
subject/email -- agents/identity.py) *and* a chat session id, so one user
can never load another's conversation: the API derives ``actor_id`` from
its own verified session and the AgentCore runtime only accepts IAM-signed
calls from the API (the Task 3.5 actor-isolation finding, closed by
design).

Long-term strategies (summaries, preferences, semantic facts) are not
configured -- release 1 keeps only short-term conversational memory.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Protocol

from strands.types.content import Messages

MAX_TURNS = 6
MAX_STORED_ANSWER_CHARS = 6000


class ConversationStore(Protocol):
    def load(self, actor_id: str, session_id: str) -> Messages: ...

    def append(
        self, actor_id: str, session_id: str, prompt: str, answer: str
    ) -> None: ...

    def delete_actor(self, actor_id: str) -> int: ...


def _as_messages(turns: list[tuple[str, str]]) -> Messages:
    messages: Messages = []
    for prompt, answer in turns[-MAX_TURNS:]:
        messages.append({"role": "user", "content": [{"text": prompt}]})
        messages.append({"role": "assistant", "content": [{"text": answer}]})
    return messages


class InMemoryConversationStore:
    """Process-local store for local development and the in-process
    fallback. Lost on restart, like the API's in-memory sessions."""

    def __init__(self) -> None:
        self._turns: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        self._lock = threading.Lock()

    def load(self, actor_id: str, session_id: str) -> Messages:
        with self._lock:
            return _as_messages(list(self._turns.get((actor_id, session_id), [])))

    def append(self, actor_id: str, session_id: str, prompt: str, answer: str) -> None:
        with self._lock:
            turns = self._turns[(actor_id, session_id)]
            turns.append((prompt, answer[:MAX_STORED_ANSWER_CHARS]))
            del turns[:-MAX_TURNS]

    def delete_actor(self, actor_id: str) -> int:
        with self._lock:
            keys = [k for k in self._turns if k[0] == actor_id]
            count = sum(len(self._turns[k]) for k in keys)
            for key in keys:
                del self._turns[key]
            return count


class AgentCoreConversationStore:
    """AgentCore Memory (data plane) short-term events: one event per turn
    holding the USER and ASSISTANT messages."""

    def __init__(
        self, *, memory_id: str, region: str, client: Any | None = None
    ) -> None:
        self._memory_id = memory_id
        if client is None:
            import boto3

            client = boto3.client("bedrock-agentcore", region_name=region)
        self._client = client

    def load(self, actor_id: str, session_id: str) -> Messages:
        response = self._client.list_events(
            memoryId=self._memory_id,
            actorId=actor_id,
            sessionId=session_id,
            includePayloads=True,
            maxResults=MAX_TURNS * 2,
        )
        events = sorted(
            response.get("events", []), key=lambda e: e.get("eventTimestamp") or 0
        )
        turns: list[tuple[str, str]] = []
        for event in events:
            prompt = answer = None
            for item in event.get("payload", []):
                message = item.get("conversational") or {}
                text = (message.get("content") or {}).get("text")
                if message.get("role") == "USER":
                    prompt = text
                elif message.get("role") == "ASSISTANT":
                    answer = text
            if prompt and answer:
                turns.append((prompt, answer))
        return _as_messages(turns)

    def append(self, actor_id: str, session_id: str, prompt: str, answer: str) -> None:
        self._client.create_event(
            memoryId=self._memory_id,
            actorId=actor_id,
            sessionId=session_id,
            eventTimestamp=datetime.now(UTC),
            payload=[
                {"conversational": {"content": {"text": prompt}, "role": "USER"}},
                {
                    "conversational": {
                        "content": {"text": answer[:MAX_STORED_ANSWER_CHARS]},
                        "role": "ASSISTANT",
                    }
                },
            ],
        )

    def delete_actor(self, actor_id: str) -> int:
        """Requirement 11.8's memory deletion: every event of every session
        this actor has. Returns how many events were deleted."""
        deleted = 0
        sessions = self._client.list_sessions(
            memoryId=self._memory_id, actorId=actor_id, maxResults=100
        ).get("sessionSummaries", [])
        for session in sessions:
            session_id = session["sessionId"]
            while True:
                events = self._client.list_events(
                    memoryId=self._memory_id,
                    actorId=actor_id,
                    sessionId=session_id,
                    includePayloads=False,
                    maxResults=100,
                ).get("events", [])
                if not events:
                    break
                for event in events:
                    self._client.delete_event(
                        memoryId=self._memory_id,
                        actorId=actor_id,
                        sessionId=session_id,
                        eventId=event["eventId"],
                    )
                    deleted += 1
                time.sleep(0.05)
        return deleted
