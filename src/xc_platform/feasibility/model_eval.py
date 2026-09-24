"""Task 3.6 -- Bedrock model quality, latency, and cost evaluation (gate F7).

Two evaluation sets, both derived from approved local sources:

1. **Analytics fixtures** -- questions whose ground truth is computed directly
   from the snapshot with SQL. The model must reach the same number through its
   tools. Any deviation is a failure; there is no partial credit for prose.

2. **Resolution decisions** -- pairs curated in `name_corrections.csv` and
   `DUPLICATE_NAMES_REPORT.md`. Gate F7 requires **100% separation of
   confirmed-distinct pairs**; a false merge is treated as a disqualifying
   failure, not a scored miss, because merging two real children's records is
   far harder to repair than leaving a duplicate.

The evaluation set contains athlete names, which are public race results but
still youth data. Names are used in-memory for scoring only. The persisted
artifact records pair *ids* and verdicts, never the roster.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

# Column mapping for the feasibility snapshot (built from the baseline CSV).
COL = {
    "athlete": "c2",
    "gender": "c3",
    "finish_s": "c7",
    "season": "c8",
    "meet_number": "c9",
    "division": "c13",
    "team": "c6",
    "grade": "c37",
    "place": "c0",
}


@dataclass
class AnalyticsFixture:
    """One question with a deterministic, SQL-derived expected answer."""

    fixture_id: str
    question: str
    ground_truth_sql: str
    expected: Any = None
    tolerance: float = 0.0

    def compute(self, connection: sqlite3.Connection) -> Any:
        value = connection.execute(self.ground_truth_sql).fetchone()[0]
        self.expected = value
        return value

    def matches(self, answer_text: str) -> bool:
        """Check whether the model's prose contains the exact expected value."""
        if self.expected is None:
            return False
        numbers = _numbers_in(answer_text)
        expected = float(self.expected)
        return any(abs(n - expected) <= self.tolerance for n in numbers)


def _numbers_in(text: str) -> list[float]:
    """Pull numeric tokens out of prose, tolerating thousands separators."""
    out: list[float] = []
    for token in re.findall(r"-?\d[\d,]*\.?\d*", text or ""):
        try:
            out.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return out


def build_analytics_fixtures() -> list[AnalyticsFixture]:
    """Questions a dashboard user would actually ask, with exact answers."""
    a, s, m, d, t = (
        COL["athlete"],
        COL["season"],
        COL["meet_number"],
        COL["division"],
        COL["team"],
    )
    return [
        AnalyticsFixture(
            "total_results",
            "How many race results are in the dataset?",
            "SELECT COUNT(*) FROM results",
        ),
        AnalyticsFixture(
            "distinct_athletes",
            "How many distinct athletes appear in the dataset?",
            f"SELECT COUNT(DISTINCT {a}) FROM results WHERE {a} <> ''",
        ),
        AnalyticsFixture(
            "distinct_teams",
            "How many distinct teams are in the dataset?",
            f"SELECT COUNT(DISTINCT {t}) FROM results WHERE {t} <> ''",
        ),
        AnalyticsFixture(
            "results_2024",
            "How many results are from the 2024 season?",
            f"SELECT COUNT(*) FROM results WHERE {s} = '2024'",
        ),
        AnalyticsFixture(
            "results_2023_meet1",
            "How many results are from meet 1 of the 2023 season?",
            f"SELECT COUNT(*) FROM results WHERE {s} = '2023' AND {m} = '1'",
        ),
        AnalyticsFixture(
            "varsity_2025",
            "How many Varsity results are there in the 2025 season?",
            f"SELECT COUNT(*) FROM results WHERE {s} = '2025' AND {d} = 'Varsity'",
        ),
        AnalyticsFixture(
            "seasons_covered",
            "How many distinct seasons does the dataset cover?",
            f"SELECT COUNT(DISTINCT {s}) FROM results WHERE {s} <> ''",
        ),
    ]


@dataclass
class ResolutionCase:
    """One identity decision drawn from the curated corrections."""

    case_id: str
    name_a: str
    name_b: str
    team: str
    expected: str  # MATCH | REVIEW | KEEP_SEPARATE
    rationale: str
    disqualifying_if_matched: bool = False


def build_resolution_cases() -> list[ResolutionCase]:
    """Curated from name_corrections.csv and DUPLICATE_NAMES_REPORT.md."""
    return [
        # Confirmed-distinct: merging any of these is a disqualifying failure.
        ResolutionCase(
            "distinct_smolinski",
            "Gianna Smolinski",
            "Giovanni Smolinski",
            "St John the Evangelist",
            "KEEP_SEPARATE",
            "Curated decision: different people (siblings).",
            disqualifying_if_matched=True,
        ),
        ResolutionCase(
            "distinct_pleva",
            "Luke Pleva",
            "Blake Pleva",
            "Nativity",
            "KEEP_SEPARATE",
            "Curated decision: different people (siblings).",
            disqualifying_if_matched=True,
        ),
        ResolutionCase(
            "distinct_shewangzaw",
            "Anna Shewangzaw",
            "Adam Shewangzaw",
            "Q of A",
            "KEEP_SEPARATE",
            "Curated decision: different people (siblings).",
            disqualifying_if_matched=True,
        ),
        ResolutionCase(
            "distinct_buechel",
            "Carly Buechel",
            "Charlie Buechel",
            "Blessed Sacrament",
            "KEEP_SEPARATE",
            "Curated decision: different people.",
            disqualifying_if_matched=True,
        ),
        # Ambiguous: the correct behavior is to escalate, not to decide.
        ResolutionCase(
            "ambiguous_niez",
            "Liam Niez",
            "William Niez",
            "St Agnes",
            "REVIEW",
            "Could be a nickname or two siblings; flagged for human review.",
        ),
        ResolutionCase(
            "ambiguous_brown",
            "Kaitlyn Brown",
            "Katelynn Brown",
            "All Saints",
            "REVIEW",
            "Both spellings valid; flagged for human review.",
        ),
        ResolutionCase(
            "ambiguous_fitzgibbon",
            "Elise Fitzgibbon",
            "Emilie Fitzgibbon",
            "St Mark",
            "REVIEW",
            "Similar but distinct given names; flagged for human review.",
        ),
        ResolutionCase(
            "ambiguous_guadalupe",
            "Angelika Guadalupe-Canales",
            "Angeliz Guadalupe-Canales",
            "Basilica of St Mary",
            "REVIEW",
            "Multiple spelling variants across years; flagged for review.",
        ),
        # Confirmed-same: curated 'apply' corrections.
        ResolutionCase(
            "same_fischer",
            "Gwen Fischer",
            "Gwendolyn Fischer",
            "St Agnes",
            "MATCH",
            "Curated 'apply': nickname to full name.",
        ),
        ResolutionCase(
            "same_kennedy",
            "Charlie Kennedy",
            "Charlotte Kennedy",
            "St Agnes",
            "MATCH",
            "Curated 'apply': confirmed same person.",
        ),
    ]


RESOLUTION_PROMPT = """You decide whether two athlete names from a youth
cross-country database refer to the SAME person.

Rules you must follow:
- Reply with exactly one JSON object and nothing else.
- Schema: {"disposition": "MATCH" | "CREATE" | "REVIEW", "confidence": 0.0-1.0,
  "reason": "<one short sentence>"}
- MATCH only when the evidence is conclusive (for example a well-known
  nickname of the same given name, or a spelling/capitalization variant).
- Different given names that merely share a surname and team are NOT a match.
  Siblings are common in this league.
- When the evidence is genuinely ambiguous, answer REVIEW. Escalating is
  always acceptable; a wrong MATCH merges two real children's records and is
  the worst possible outcome.

Candidate A: {name_a}
Candidate B: {name_b}
Team (both): {team}
Both names appear in the same league across overlapping seasons.
"""


def parse_disposition(text: str) -> tuple[str | None, float | None, str]:
    """Extract the JSON verdict from a model reply, tolerating stray prose."""
    raw = text or ""
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None, None, "no JSON object in reply"
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None, None, "unparseable JSON in reply"
    disposition = str(parsed.get("disposition", "")).upper().strip()
    if disposition not in {"MATCH", "CREATE", "REVIEW"}:
        return None, None, f"unknown disposition: {disposition!r}"
    confidence = parsed.get("confidence")
    return (
        disposition,
        float(confidence) if isinstance(confidence, (int, float)) else None,
        str(parsed.get("reason", ""))[:200],
    )


def score_resolution(case: ResolutionCase, disposition: str | None) -> dict[str, Any]:
    """Score one decision. A false merge is disqualifying, not merely wrong."""
    if disposition is None:
        return {"correct": False, "false_merge": False, "outcome": "unparseable"}
    if case.disqualifying_if_matched and disposition == "MATCH":
        return {"correct": False, "false_merge": True, "outcome": "FALSE MERGE"}
    if case.expected == "KEEP_SEPARATE":
        # Either explicitly separating or escalating preserves distinctness.
        correct = disposition in {"CREATE", "REVIEW"}
        return {
            "correct": correct,
            "false_merge": False,
            "outcome": "separated" if correct else "unexpected",
        }
    correct = disposition == case.expected
    return {
        "correct": correct,
        "false_merge": False,
        "outcome": "exact" if correct else f"got {disposition}",
    }


@dataclass
class ModelRun:
    """Accumulated measurements for one model across both evaluation sets."""

    model_id: str
    analytics: list[dict[str, Any]] = field(default_factory=list)
    resolution: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        analytics_correct = sum(1 for r in self.analytics if r.get("correct"))
        resolution_correct = sum(1 for r in self.resolution if r.get("correct"))
        false_merges = sum(1 for r in self.resolution if r.get("false_merge"))
        input_tokens = sum(r.get("input_tokens", 0) for r in self.all_rows())
        output_tokens = sum(r.get("output_tokens", 0) for r in self.all_rows())
        latencies = [r["latency_s"] for r in self.all_rows() if r.get("latency_s")]
        latencies.sort()
        return {
            "model_id": self.model_id,
            "analytics_correct": analytics_correct,
            "analytics_total": len(self.analytics),
            "resolution_correct": resolution_correct,
            "resolution_total": len(self.resolution),
            "false_merges": false_merges,
            "confirmed_distinct_separation": (
                "100%" if false_merges == 0 else f"FAILED ({false_merges})"
            ),
            "tool_calls": sum(r.get("tool_calls", 0) for r in self.analytics),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "latency_p50_s": latencies[len(latencies) // 2] if latencies else None,
            "latency_max_s": latencies[-1] if latencies else None,
            "errors": self.errors,
        }

    def all_rows(self) -> list[dict[str, Any]]:
        return [*self.analytics, *self.resolution]


def estimate_monthly_cost(
    summary: dict[str, Any],
    *,
    input_usd_per_million: float | None,
    output_usd_per_million: float | None,
    questions_per_month: int,
) -> dict[str, Any]:
    """Project monthly model spend from measured per-question token usage.

    Prices are **inputs**, not assumptions: current-generation Bedrock model
    prices are not machine-retrievable (the Pricing API carries only legacy
    Claude models, and the pricing page renders prices client-side), so the
    owner supplies them at the Task 3.8 gate.
    """
    rows = summary["analytics_total"] + summary["resolution_total"]
    if not rows:
        return {"error": "no measured rows"}
    input_per_question = summary["input_tokens"] / rows
    output_per_question = summary["output_tokens"] / rows
    result = {
        "measured_input_tokens_per_question": round(input_per_question, 1),
        "measured_output_tokens_per_question": round(output_per_question, 1),
        "assumed_questions_per_month": questions_per_month,
        "projected_input_tokens_per_month": int(
            input_per_question * questions_per_month
        ),
        "projected_output_tokens_per_month": int(
            output_per_question * questions_per_month
        ),
    }
    if input_usd_per_million is None or output_usd_per_million is None:
        result["projected_usd_per_month"] = None
        result["note"] = (
            "Supply current Bedrock per-million-token prices to complete this "
            "estimate; they are deliberately not hard-coded."
        )
        return result
    monthly = (
        input_per_question * questions_per_month / 1_000_000 * input_usd_per_million
        + output_per_question * questions_per_month / 1_000_000 * output_usd_per_million
    )
    result["input_usd_per_million"] = input_usd_per_million
    result["output_usd_per_million"] = output_usd_per_million
    result["projected_usd_per_month"] = round(monthly, 4)
    return result
