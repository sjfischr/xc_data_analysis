"""A scripted, local ``strands.models.Model`` implementation for tests.

Test-only. Exercises the real Strands agent loop -- tool dispatch, message
formatting, stop-reason handling -- without any network call or AWS
credential, so agent-wiring tests run in ordinary CI (design.md 12.2's
"emergency in-process development mode" is a production fallback; this is
narrower still -- a fixed script, not a real model, used only under
``pytest``).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterable
from dataclasses import dataclass, field
from typing import Any

from strands.models.model import Model
from strands.types.content import Messages
from strands.types.streaming import StreamEvent
from strands.types.tools import ToolChoice, ToolSpec


@dataclass(frozen=True, slots=True)
class ToolCallTurn:
    tool_name: str
    tool_input: dict[str, Any]


@dataclass(frozen=True, slots=True)
class TextTurn:
    text: str


@dataclass
class FakeModel(Model):
    """Replays ``script`` one turn per :meth:`stream` call. A
    :class:`ToolCallTurn` yields a tool-use stop; a :class:`TextTurn` yields
    a final-answer stop. Every call is recorded in ``calls`` for
    assertions.

    ``loop=True`` wraps back to the start of ``script`` once exhausted
    instead of raising -- for proving a runaway-protection cap (design.md
    15.3) actually stops an agent that would otherwise call a tool forever,
    without needing an absurdly long fixed script."""

    script: list[ToolCallTurn | TextTurn]
    loop: bool = False
    calls: list[Messages] = field(default_factory=list)
    _index: int = 0

    def update_config(self, **model_config: Any) -> None:
        return None

    def get_config(self) -> Any:
        return {}

    async def structured_output(
        self,
        output_model: Any,
        prompt: Messages,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError("FakeModel does not support structured_output")
        yield {}  # type: ignore[unreachable]  # makes this an async generator

    async def stream(
        self,
        messages: Messages,
        tool_specs: list[ToolSpec] | None = None,
        system_prompt: str | None = None,
        *,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[StreamEvent]:
        self.calls.append(messages)
        if self._index >= len(self.script):
            if self.loop and self.script:
                self._index = 0
            else:
                raise AssertionError(
                    f"FakeModel script exhausted after {len(self.script)} turn(s); "
                    "the agent asked for another turn"
                )
        turn = self.script[self._index]
        self._index += 1

        yield {"messageStart": {"role": "assistant"}}

        if isinstance(turn, ToolCallTurn):
            tool_use_id = f"fake-{uuid.uuid4().hex[:8]}"
            yield {
                "contentBlockStart": {
                    "contentBlockIndex": 0,
                    "start": {
                        "toolUse": {"name": turn.tool_name, "toolUseId": tool_use_id}
                    },
                }
            }
            yield {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"toolUse": {"input": json.dumps(turn.tool_input)}},
                }
            }
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "tool_use"}}
        else:
            yield {"contentBlockStart": {"contentBlockIndex": 0, "start": {}}}
            yield {
                "contentBlockDelta": {
                    "contentBlockIndex": 0,
                    "delta": {"text": turn.text},
                }
            }
            yield {"contentBlockStop": {"contentBlockIndex": 0}}
            yield {"messageStop": {"stopReason": "end_turn"}}

        yield {
            "metadata": {
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            }
        }
