"""The eval's rubric judge fails closed (scripts/agent_eval.py).

Found in review (2026-10-17): the first version passed an empty results
list, a reply that skipped criteria, and a string "false" verdict.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def agent_eval() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "agent_eval", ROOT / "scripts" / "agent_eval.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["agent_eval"] = module
    spec.loader.exec_module(module)
    return module


def _reply(*items: dict[str, object]) -> str:
    return "Here you go: " + json.dumps({"results": list(items)})


def test_all_criteria_passing_is_clean(agent_eval: ModuleType) -> None:
    reply = _reply(
        {"criterion": 1, "pass": True, "why": "ok"},
        {"criterion": 2, "pass": True, "why": "ok"},
    )
    assert agent_eval.rubric_problems(reply, 2) == []


def test_a_failed_criterion_is_reported_with_its_reason(agent_eval: ModuleType) -> None:
    reply = _reply(
        {"criterion": 1, "pass": True, "why": "ok"},
        {"criterion": 2, "pass": False, "why": "no criteria stated"},
    )
    assert agent_eval.rubric_problems(reply, 2) == ["rubric 2: no criteria stated"]


def test_empty_results_fail_every_criterion(agent_eval: ModuleType) -> None:
    assert agent_eval.rubric_problems(_reply(), 2) == [
        "rubric 1: judge gave no verdict",
        "rubric 2: judge gave no verdict",
    ]


def test_skipped_criterion_fails(agent_eval: ModuleType) -> None:
    reply = _reply({"criterion": 1, "pass": True, "why": "ok"})
    assert agent_eval.rubric_problems(reply, 2) == ["rubric 2: judge gave no verdict"]


@pytest.mark.parametrize("verdict", ["false", "true", 1, 0, None])
def test_non_boolean_verdict_fails(agent_eval: ModuleType, verdict: object) -> None:
    reply = _reply({"criterion": 1, "pass": verdict, "why": "?"})
    [problem] = agent_eval.rubric_problems(reply, 1)
    assert problem.startswith("rubric 1: verdict") and "not a boolean" in problem


def test_duplicate_and_out_of_range_criteria_fail(agent_eval: ModuleType) -> None:
    reply = _reply(
        {"criterion": 1, "pass": True, "why": "ok"},
        {"criterion": 1, "pass": True, "why": "again"},
        {"criterion": 3, "pass": True, "why": "no such criterion"},
        {"criterion": True, "pass": True, "why": "bool is not a number"},
    )
    problems = agent_eval.rubric_problems(reply, 1)
    assert "rubric 1: judge gave more than one verdict" in problems
    assert sum(p.startswith("judge reply malformed") for p in problems) == 2


@pytest.mark.parametrize(
    "reply", ["no json here", '{"verdicts": []}', '{"results": "all good"}']
)
def test_unreadable_or_malformed_reply_fails(
    agent_eval: ModuleType, reply: str
) -> None:
    [problem] = agent_eval.rubric_problems(reply, 2)
    assert problem.startswith(("judge reply unreadable", "judge reply malformed"))


def test_improve_chance_bans_slightly_better_only_about_the_athletes(
    agent_eval: ModuleType,
) -> None:
    [case] = [c for c in agent_eval.CASES if c.key == "improve_chance"]

    def flagged(text: str) -> bool:
        return any(re.search(p, text.lower()) for p in case.must_not_match)

    assert flagged("Teddy Cypher has a slightly better chance than Liam.")
    assert flagged("Liam has a marginally better shot at holding on.")
    assert not flagged(
        "The model's typical miss is only marginally better than assuming "
        "standing stays flat."
    )
