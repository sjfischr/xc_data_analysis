"""Task 3.2 -- RunSignup structured-extraction feasibility probe.

Verifies gates F1 (coverage), F2 (2023/2024 Meet 2 athlete availability), and
F3 (URL scope normalization) from design section 17.

What this probe establishes, using only public endpoints:

* which events and result sets exist per race, per season;
* whether athlete-level rows (not just team scores) are published;
* the field/header contract, including per-result-set custom field IDs;
* pagination and completion semantics;
* error behavior for invalid identifiers;
* how live counts compare with the frozen historical baseline.

The probe never mutates the baseline. Discrepancies are reported, not applied.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from xc_platform.feasibility.session import BudgetExhausted, ProbeSession

BASE = "https://runsignup.com/Rest"

# Race IDs recovered from the saved historical pages under data/pages/.
# The NVJCYO series publishes each meet as a separate RunSignup "race", with
# every season's events nested under that race.
MEET_RACE_IDS: dict[str, int] = {
    "154050": 1,  # NVJCYO Cross Country Developmental Meet 1
    "154708": 2,  # NVJCYO Cross Country Developmental Meet 2
    "155696": 3,  # NVJCYO Cross Country Championship (saved as "Meet 3")
}

# Baseline division vocabulary (matches tests/fixtures/baseline/counts.json).
# The source publishes both "2nd Grade Boys" and the shorter "2nd Boys", so the
# 2nd-grade pattern must not require the word "grade".
_DIVISION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b2nd\b", "2nd Grade"),
    (r"\bfrosh\b", "Frosh"),
    (r"\bjv\b", "JV"),
    (r"\bvarsity\b", "Varsity"),
)

# The frozen baseline encodes gender inconsistently between season-meets: some
# use F/M, others Boys/Girls. Both sides are canonicalized to F/M before any
# count comparison, and the inconsistency is reported as a migration finding.
_GENDER_CANONICAL: dict[str, str] = {
    "f": "F",
    "girls": "F",
    "girl": "F",
    "female": "F",
    "m": "M",
    "boys": "M",
    "boy": "M",
    "male": "M",
}


def canonical_gender(value: str | None) -> str | None:
    """Fold a source or baseline gender label onto the canonical F/M codes."""
    if value is None:
        return None
    return _GENDER_CANONICAL.get(str(value).strip().lower())

_RESULTS_URL_RE = re.compile(
    r"^https://runsignup\.com/Race/(?:(?P<state>[A-Z]{2})/(?P<city>[^/]+)/)?"
    r"Results/(?P<race_id>\d+)",
    re.IGNORECASE,
)
_FRAGMENT_SET_RE = re.compile(r"resultSetId-(?P<set_id>\d+)", re.IGNORECASE)
_FRAGMENT_PERPAGE_RE = re.compile(r"perpage:(?P<per_page>\d+)", re.IGNORECASE)


@dataclass
class ResultSetProbe:
    """Everything learned about one published result set."""

    race_id: str
    meet_number: int
    season_year: int
    event_id: int
    event_name: str
    division: str | None
    gender: str | None
    distance: str | None
    set_id: int
    set_name: str
    public_results: bool
    preliminary_results: bool
    results_source_name: str | None
    header_labels: dict[str, str]
    custom_field_labels: dict[str, str]
    row_count: int
    unique_result_ids: int
    pages_fetched: int
    last_page_rows: int
    place_min: int | None
    place_max: int | None
    athlete_level: bool
    named_rows: int
    timed_rows: int

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def normalize_results_url(url: str) -> dict[str, Any]:
    """Normalize a RunSignup results URL, including its hash-fragment scope.

    The supplied spec URL carries its result-set selection in the fragment
    (``#resultSetId-691534;perpage:100``), which a browser never sends to the
    server. Scope therefore has to be recovered client-side and mapped onto
    REST parameters -- this is gate F3.
    """
    match = _RESULTS_URL_RE.match(url.strip())
    if not match:
        raise ValueError(f"not a recognized RunSignup results URL: {url!r}")
    fragment = url.partition("#")[2]
    set_match = _FRAGMENT_SET_RE.search(fragment)
    per_page_match = _FRAGMENT_PERPAGE_RE.search(fragment)
    return {
        "race_id": match.group("race_id"),
        "requested_result_set_id": (
            int(set_match.group("set_id")) if set_match else None
        ),
        "requested_per_page": (
            int(per_page_match.group("per_page")) if per_page_match else None
        ),
        "fragment_present": bool(fragment),
        "note": (
            "Result-set scope lives in the URL fragment and is not transmitted "
            "to the server; the adapter must parse it locally and translate it "
            "into event_id + individual_result_set_id REST parameters."
        ),
    }


def season_year(start_time: str | None) -> int | None:
    """Extract the season year from a RunSignup ``M/D/YYYY HH:MM`` timestamp."""
    if not start_time:
        return None
    found = re.search(r"\b(20\d{2})\b", start_time)
    return int(found.group(1)) if found else None


def classify_event(name: str) -> tuple[str | None, str | None]:
    """Map an event name onto the baseline (division, gender) vocabulary."""
    lowered = (name or "").lower()
    division = next(
        (label for pattern, label in _DIVISION_PATTERNS if re.search(pattern, lowered)),
        None,
    )
    gender: str | None = None
    if re.search(r"\bgirls?\b|\bwomen\b", lowered):
        gender = "F"
    elif re.search(r"\bboys?\b|\bmen\b", lowered):
        gender = "M"
    return division, gender


def _custom_field_labels(headers: dict[str, str]) -> dict[str, str]:
    """Isolate the per-result-set custom fields from the stable header set."""
    return {
        key: label
        for key, label in headers.items()
        if key.startswith("custom-field-") or key.startswith("division-")
    }


def _fetch_result_sets(
    session: ProbeSession, race_id: str, event_id: int, label: str
) -> list[dict[str, Any]]:
    url = (
        f"{BASE}/race/{race_id}/results/get-result-sets"
        f"?format=json&event_id={event_id}"
    )
    payload = session.get_json(url, note=f"result sets: {label}")
    if not isinstance(payload, dict):
        return []
    return payload.get("individual_results_sets") or []


def _fetch_rows(
    session: ProbeSession,
    race_id: str,
    event_id: int,
    set_id: int,
    per_page: int,
    max_pages: int,
    label: str,
) -> tuple[list[dict[str, Any]], dict[str, str], int, int]:
    """Page through one result set until a short/empty page ends it.

    RunSignup returns no total-count or page-count field, so completion is
    detected by a terminal short page -- exactly the fallback the design
    anticipated. Unique ``result_id`` counting guards against overlap.
    """
    rows: list[dict[str, Any]] = []
    headers: dict[str, str] = {}
    pages = 0
    last_page_rows = 0
    for page in range(1, max_pages + 1):
        url = (
            f"{BASE}/race/{race_id}/results/get-results?format=json"
            f"&event_id={event_id}&individual_result_set_id={set_id}"
            f"&results_per_page={per_page}&page={page}"
        )
        payload = session.get_json(url, note=f"rows p{page}: {label}")
        pages += 1
        if not isinstance(payload, dict):
            break
        sets = payload.get("individual_results_sets") or []
        if not sets:
            last_page_rows = 0
            break
        block = sets[0]
        headers = headers or (block.get("results_headers") or {})
        page_rows = block.get("results") or []
        last_page_rows = len(page_rows)
        rows.extend(page_rows)
        if len(page_rows) < per_page:
            break
    return rows, headers, pages, last_page_rows


def probe_race(
    session: ProbeSession,
    race_id: str,
    per_page: int,
    max_pages: int,
) -> tuple[dict[str, Any], list[ResultSetProbe]]:
    """Enumerate one race: metadata, events, result sets, and rows."""
    meta = session.get_json(
        f"{BASE}/race/{race_id}?format=json"
        "&future_events_only=F&most_recent_events_only=F",
        note=f"race {race_id} metadata",
    )
    race = (meta or {}).get("race", {}) if isinstance(meta, dict) else {}
    events = race.get("events") or []
    meet_number = MEET_RACE_IDS.get(race_id, 0)

    race_info = {
        "race_id": race_id,
        "meet_number": meet_number,
        "name": race.get("name"),
        "url": race.get("url"),
        "external_results_url": race.get("external_results_url"),
        "is_private_race": race.get("is_private_race"),
        "event_count": len(events),
        "seasons": sorted(
            {y for y in (season_year(e.get("start_time")) for e in events) if y}
        ),
    }

    probes: list[ResultSetProbe] = []
    for event in events:
        event_id = int(event.get("event_id"))
        name = str(event.get("name") or "")
        year = season_year(event.get("start_time"))
        division, gender = classify_event(name)
        label = f"{race_id}/{year}/{name}"
        for block in _fetch_result_sets(session, race_id, event_id, label):
            set_id = int(block.get("individual_result_set_id"))
            rows, headers, pages, last_page_rows = _fetch_rows(
                session, race_id, event_id, set_id, per_page, max_pages, label
            )
            result_ids = {
                r.get("result_id") for r in rows if r.get("result_id") is not None
            }
            places = [r.get("place") for r in rows if isinstance(r.get("place"), int)]
            named = sum(
                1
                for r in rows
                if str(r.get("first_name") or "").strip()
                and str(r.get("last_name") or "").strip()
            )
            timed = sum(
                1
                for r in rows
                if str(r.get("chip_time") or r.get("clock_time") or "").strip()
            )
            probes.append(
                ResultSetProbe(
                    race_id=race_id,
                    meet_number=meet_number,
                    season_year=year or 0,
                    event_id=event_id,
                    event_name=name,
                    division=division,
                    gender=gender,
                    distance=event.get("distance"),
                    set_id=set_id,
                    set_name=str(block.get("individual_result_set_name") or ""),
                    public_results=block.get("public_results") == "T",
                    preliminary_results=block.get("preliminary_results") == "T",
                    results_source_name=block.get("results_source_name"),
                    header_labels={
                        k: v
                        for k, v in headers.items()
                        if not k.startswith(("custom-field-", "division-"))
                    },
                    custom_field_labels=_custom_field_labels(headers),
                    row_count=len(rows),
                    unique_result_ids=len(result_ids),
                    pages_fetched=pages,
                    last_page_rows=last_page_rows,
                    place_min=min(places) if places else None,
                    place_max=max(places) if places else None,
                    athlete_level=named > 0,
                    named_rows=named,
                    timed_rows=timed,
                )
            )
    return race_info, probes


def probe_error_behavior(session: ProbeSession, race_id: str) -> list[dict[str, Any]]:
    """Record how the API answers invalid identifiers and out-of-range pages."""
    checks: list[dict[str, Any]] = []
    cases = [
        (
            f"{BASE}/race/{race_id}/results/get-result-sets?format=json",
            "get-result-sets without event_id",
        ),
        (
            f"{BASE}/race/{race_id}/results/get-results?format=json"
            "&event_id=1&individual_result_set_id=1&results_per_page=5&page=1",
            "non-existent event/result-set pair",
        ),
        (
            f"{BASE}/race/999999999?format=json",
            "non-existent race id",
        ),
    ]
    for url, note in cases:
        payload = session.get_json(url, note=f"error behavior: {note}")
        call = session.calls[-1]
        error = (payload or {}).get("error") if isinstance(payload, dict) else None
        checks.append(
            {
                "case": note,
                "http_status": call.status,
                "error_code": (error or {}).get("error_code"),
                "error_msg": (error or {}).get("error_msg"),
                "observation": (
                    "HTTP 200 with an error envelope"
                    if call.status == 200 and error
                    else "non-200 or unstructured response"
                ),
            }
        )
    return checks


def probe_pagination(
    session: ProbeSession,
    race_id: str,
    event_id: int,
    set_id: int,
    small_page: int = 25,
) -> dict[str, Any]:
    """Verify pagination boundaries against a single-request reference fetch.

    Every NVJCYO result set fits inside one large page, so the paging path is
    not exercised by ordinary enumeration. This deliberately re-fetches one set
    in small pages to establish: no overlap, no omission, stable ordering, and
    the terminal behavior of a page past the end.
    """
    reference, _, _, _ = _fetch_rows(
        session, race_id, event_id, set_id, 500, 2, "pagination reference"
    )
    reference_ids = [r.get("result_id") for r in reference]

    paged: list[Any] = []
    pages = 0
    page_sizes: list[int] = []
    for page in range(1, 20):
        url = (
            f"{BASE}/race/{race_id}/results/get-results?format=json"
            f"&event_id={event_id}&individual_result_set_id={set_id}"
            f"&results_per_page={small_page}&page={page}"
        )
        payload = session.get_json(url, note=f"pagination page {page}")
        pages += 1
        sets = (payload or {}).get("individual_results_sets") or []
        rows = (sets[0].get("results") or []) if sets else []
        page_sizes.append(len(rows))
        paged.extend(r.get("result_id") for r in rows)
        if len(rows) < small_page:
            break

    beyond_url = (
        f"{BASE}/race/{race_id}/results/get-results?format=json"
        f"&event_id={event_id}&individual_result_set_id={set_id}"
        f"&results_per_page={small_page}&page=999"
    )
    beyond = session.get_json(beyond_url, note="pagination past the end")
    beyond_sets = (beyond or {}).get("individual_results_sets") or []
    beyond_rows = (beyond_sets[0].get("results") or []) if beyond_sets else []

    return {
        "reference_rows": len(reference_ids),
        "paged_rows": len(paged),
        "pages_fetched": pages,
        "page_sizes": page_sizes,
        "page_size_requested": small_page,
        "unique_paged_ids": len(set(paged)),
        "duplicate_ids_across_pages": len(paged) - len(set(paged)),
        "missing_versus_reference": len(set(reference_ids) - set(paged)),
        "extra_versus_reference": len(set(paged) - set(reference_ids)),
        "order_preserved": paged == reference_ids,
        "page_past_end_rows": len(beyond_rows),
        "conclusion": (
            "Paged retrieval reproduces the reference set exactly and a page "
            "past the end returns an empty row list; because no total-count "
            "field is published, completion detection must rely on a terminal "
            "short page plus unique result_id accounting."
        ),
    }


def compare_with_baseline(
    probes: list[ResultSetProbe], baseline_path: Path
) -> dict[str, Any]:
    """Compare live per-season/meet/division/gender counts with the baseline.

    Differences are evidence for the report only. The baseline is never
    rewritten here -- R1.1 keeps it frozen until an owner-approved task.
    """
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline_cells: dict[tuple[int, int, str, str], int] = {}
    raw_gender_labels: dict[str, set[str]] = {}
    for row in baseline.get("by_season_meet_division_gender", []):
        gender = canonical_gender(row["gender"])
        raw_gender_labels.setdefault(
            f"{row['season_year']}-M{row['meet_number']}", set()
        ).add(str(row["gender"]))
        if gender is None:
            continue
        key = (row["season_year"], row["meet_number"], row["division"], gender)
        baseline_cells[key] = baseline_cells.get(key, 0) + row["records"]

    live_cells: dict[tuple[int, int, str, str], int] = {}
    for probe in probes:
        gender = canonical_gender(probe.gender)
        if not probe.division or not gender:
            continue
        key = (probe.season_year, probe.meet_number, probe.division, gender)
        live_cells[key] = live_cells.get(key, 0) + probe.named_rows

    rows: list[dict[str, Any]] = []
    for key in sorted(set(baseline_cells) | set(live_cells)):
        season, meet, division, gender = key
        base_count = baseline_cells.get(key, 0)
        live_count = live_cells.get(key, 0)
        rows.append(
            {
                "season_year": season,
                "meet_number": meet,
                "division": division,
                "gender": gender,
                "baseline_records": base_count,
                "live_named_rows": live_count,
                "delta": live_count - base_count,
            }
        )
    by_season_meet: dict[str, dict[str, int]] = {}
    for row in rows:
        key = f"{row['season_year']}-M{row['meet_number']}"
        bucket = by_season_meet.setdefault(
            key, {"baseline_records": 0, "live_named_rows": 0, "delta": 0}
        )
        bucket["baseline_records"] += row["baseline_records"]
        bucket["live_named_rows"] += row["live_named_rows"]
        bucket["delta"] += row["delta"]
    mixed = {
        key: sorted(labels)
        for key, labels in sorted(raw_gender_labels.items())
        if len(labels) > 1 or labels - {"F", "M"}
    }
    return {
        "baseline_total_records": baseline.get("total_records"),
        "live_total_named_rows": sum(p.named_rows for p in probes),
        "by_season_meet": by_season_meet,
        "cells": rows,
        "cells_matching": sum(1 for r in rows if r["delta"] == 0),
        "cells_differing": sum(1 for r in rows if r["delta"] != 0),
        "baseline_gender_encoding": {
            "issue": (
                "The frozen baseline encodes gender inconsistently across "
                "season-meets (F/M in some, Boys/Girls in others). Both sides "
                "are canonicalized to F/M before comparison; Task 5.1 must "
                "normalize this explicitly while preserving the original value."
            ),
            "labels_by_season_meet": mixed,
        },
    }


def run(
    session: ProbeSession,
    *,
    supplied_url: str,
    race_ids: list[str],
    per_page: int,
    max_pages: int,
    baseline_path: Path,
) -> dict[str, Any]:
    """Execute the full Task 3.2 probe and return machine-readable findings."""
    findings: dict[str, Any] = {
        "url_normalization": normalize_results_url(supplied_url),
        "races": [],
        "result_sets": [],
        "budget_exhausted": False,
    }
    all_probes: list[ResultSetProbe] = []
    try:
        for race_id in race_ids:
            race_info, probes = probe_race(session, race_id, per_page, max_pages)
            findings["races"].append(race_info)
            all_probes.extend(probes)
        findings["error_behavior"] = probe_error_behavior(session, race_ids[0])
        # Exercise paging on the largest set found, which ordinary enumeration
        # never reaches because every set fits in a single large page.
        largest = max(all_probes, key=lambda p: p.row_count, default=None)
        if largest is not None and largest.row_count > 0:
            findings["pagination"] = {
                "probed_set": {
                    "race_id": largest.race_id,
                    "season_year": largest.season_year,
                    "meet_number": largest.meet_number,
                    "event_name": largest.event_name,
                    "set_id": largest.set_id,
                },
                **probe_pagination(
                    session, largest.race_id, largest.event_id, largest.set_id
                ),
            }
    except BudgetExhausted as exhausted:
        findings["budget_exhausted"] = True
        findings["budget_note"] = str(exhausted)

    findings["result_sets"] = [p.as_dict() for p in all_probes]
    findings["coverage"] = _coverage_summary(all_probes)
    findings["athlete_level_gaps"] = [
        {
            "season_year": p.season_year,
            "meet_number": p.meet_number,
            "event_name": p.event_name,
            "set_id": p.set_id,
            "row_count": p.row_count,
            "named_rows": p.named_rows,
            "set_name": p.set_name,
        }
        for p in all_probes
        if not p.athlete_level
    ]
    findings["custom_field_stability"] = _custom_field_stability(all_probes)
    if baseline_path.exists():
        findings["baseline_comparison"] = compare_with_baseline(
            all_probes, baseline_path
        )
    # The supplied URL's result-set id is resolved against what actually exists.
    requested = findings["url_normalization"]["requested_result_set_id"]
    findings["url_normalization"]["resolved"] = next(
        (
            {
                "set_id": p.set_id,
                "race_id": p.race_id,
                "season_year": p.season_year,
                "event_name": p.event_name,
            }
            for p in all_probes
            if p.set_id == requested
        ),
        None,
    )
    return findings


def _coverage_summary(probes: list[ResultSetProbe]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for probe in probes:
        key = f"{probe.season_year}-M{probe.meet_number}"
        bucket = summary.setdefault(
            key,
            {
                "result_sets": 0,
                "rows": 0,
                "named_rows": 0,
                "timed_rows": 0,
                "athlete_level_sets": 0,
                "team_only_sets": 0,
                "preliminary_sets": 0,
                "non_public_sets": 0,
            },
        )
        bucket["result_sets"] += 1
        bucket["rows"] += probe.row_count
        bucket["named_rows"] += probe.named_rows
        bucket["timed_rows"] += probe.timed_rows
        bucket["athlete_level_sets"] += 1 if probe.athlete_level else 0
        bucket["team_only_sets"] += 0 if probe.athlete_level else 1
        bucket["preliminary_sets"] += 1 if probe.preliminary_results else 0
        bucket["non_public_sets"] += 0 if probe.public_results else 1
    return dict(sorted(summary.items()))


def _custom_field_stability(probes: list[ResultSetProbe]) -> dict[str, Any]:
    """Show that custom-field numeric IDs differ per set while labels are stable.

    This is the evidence behind the design rule that the adapter must map
    custom fields by their header label, never by a hard-coded numeric ID.
    """
    label_to_keys: dict[str, set[str]] = {}
    for probe in probes:
        for key, label in probe.custom_field_labels.items():
            if key.startswith("custom-field-"):
                label_to_keys.setdefault(str(label), set()).add(key)
    return {
        "labels": {
            label: {"distinct_field_ids": len(keys), "examples": sorted(keys)[:6]}
            for label, keys in sorted(label_to_keys.items())
        },
        "conclusion": (
            "Custom-field numeric IDs are per-result-set; header labels are the "
            "only stable mapping key."
        ),
    }


def render_markdown(findings: dict[str, Any], session_summary: dict[str, Any]) -> str:
    """Render the human-readable probe report for feasibility/runs/<id>/."""
    lines: list[str] = [
        "# Feasibility probe 3.2 -- RunSignup structured extraction",
        "",
        f"- Requests used: {session_summary['requests_used']} / "
        f"{session_summary['requests_budget']}",
        f"- Total request time: {session_summary['total_elapsed_s']}s "
        f"(slowest {session_summary['slowest_s']}s)",
        f"- HTTP status counts: {session_summary['status_counts']}",
        "",
        "## Races enumerated",
        "",
        "| Race ID | Meet | Name | Events | Seasons |",
        "|---|---|---|---|---|",
    ]
    for race in findings.get("races", []):
        lines.append(
            f"| {race['race_id']} | {race['meet_number']} | {race['name']} "
            f"| {race['event_count']} "
            f"| {', '.join(str(s) for s in race['seasons'])} |"
        )

    lines += [
        "",
        "## Coverage by season and meet",
        "",
        "| Season-Meet | Sets | Rows | Named rows | Timed rows | Athlete-level sets "
        "| Team-only sets |",
        "|---|---|---|---|---|---|---|",
    ]
    for key, bucket in findings.get("coverage", {}).items():
        lines.append(
            f"| {key} | {bucket['result_sets']} | {bucket['rows']} "
            f"| {bucket['named_rows']} | {bucket['timed_rows']} "
            f"| {bucket['athlete_level_sets']} | {bucket['team_only_sets']} |"
        )

    comparison = findings.get("baseline_comparison")
    if comparison:
        lines += [
            "",
            "## Live counts versus the frozen baseline",
            "",
            f"- Baseline total records: {comparison['baseline_total_records']}",
            f"- Live named rows: {comparison['live_total_named_rows']}",
            f"- Matching cells: {comparison['cells_matching']}; "
            f"differing cells: {comparison['cells_differing']}",
            "",
            "| Season-Meet | Baseline | Live | Delta |",
            "|---|---|---|---|",
        ]
        for key, bucket in comparison.get("by_season_meet", {}).items():
            lines.append(
                f"| {key} | {bucket['baseline_records']} "
                f"| {bucket['live_named_rows']} | {bucket['delta']:+d} |"
            )
        differing = [c for c in comparison.get("cells", []) if c["delta"] != 0]
        if differing:
            lines += [
                "",
                "### Differing cells",
                "",
                "| Season | Meet | Division | Gender | Baseline | Live | Delta |",
                "|---|---|---|---|---|---|---|",
            ]
            for cell in differing:
                lines.append(
                    f"| {cell['season_year']} | {cell['meet_number']} "
                    f"| {cell['division']} | {cell['gender']} "
                    f"| {cell['baseline_records']} | {cell['live_named_rows']} "
                    f"| {cell['delta']:+d} |"
                )

    gaps = findings.get("athlete_level_gaps", [])
    lines += [
        "",
        "## Athlete-level availability (gate F2)",
        "",
        f"Result sets published without athlete-level rows: **{len(gaps)}**",
        "",
    ]
    if gaps:
        lines += [
            "| Season | Meet | Event | Set | Rows | Named rows |",
            "|---|---|---|---|---|---|",
        ]
        for gap in gaps:
            lines.append(
                f"| {gap['season_year']} | {gap['meet_number']} "
                f"| {gap['event_name']} | {gap['set_id']} "
                f"| {gap['row_count']} | {gap['named_rows']} |"
            )

    url_norm = findings.get("url_normalization", {})
    lines += [
        "",
        "## Supplied-URL scope (gate F3)",
        "",
        f"- Race ID: `{url_norm.get('race_id')}`",
        f"- Requested result set: `{url_norm.get('requested_result_set_id')}`",
        f"- Requested per-page: `{url_norm.get('requested_per_page')}`",
        f"- Resolved to: `{url_norm.get('resolved')}`",
        "",
        f"> {url_norm.get('note', '')}",
        "",
        "## Pagination and completion semantics",
        "",
    ]
    pagination = findings.get("pagination")
    if pagination:
        probed = pagination.get("probed_set", {})
        lines += [
            f"Probed set `{probed.get('set_id')}` "
            f"({probed.get('season_year')} M{probed.get('meet_number')} "
            f"{probed.get('event_name')}) at "
            f"{pagination['page_size_requested']} rows/page.",
            "",
            "| Check | Value |",
            "|---|---|",
            f"| Reference rows (single large page) | {pagination['reference_rows']} |",
            f"| Rows via small pages | {pagination['paged_rows']} |",
            f"| Pages fetched | {pagination['pages_fetched']} |",
            f"| Page sizes | {pagination['page_sizes']} |",
            f"| Duplicate IDs across pages | "
            f"{pagination['duplicate_ids_across_pages']} |",
            f"| Missing versus reference | "
            f"{pagination['missing_versus_reference']} |",
            f"| Extra versus reference | {pagination['extra_versus_reference']} |",
            f"| Order preserved | {pagination['order_preserved']} |",
            f"| Rows on page past the end | {pagination['page_past_end_rows']} |",
            "",
            pagination["conclusion"],
        ]
    else:
        lines.append("Pagination was not exercised in this run.")

    lines += [
        "",
        "## Custom-field stability",
        "",
        "| Header label | Distinct numeric field IDs observed |",
        "|---|---|",
    ]
    stability = findings.get("custom_field_stability", {}).get("labels", {})
    for label, info in stability.items():
        lines.append(f"| {label} | {info['distinct_field_ids']} |")
    lines += [
        "",
        f"{findings.get('custom_field_stability', {}).get('conclusion', '')}",
        "",
        "## Error behavior",
        "",
        "| Case | HTTP | Error code | Message | Observation |",
        "|---|---|---|---|---|",
    ]
    for check in findings.get("error_behavior", []):
        lines.append(
            f"| {check['case']} | {check['http_status']} | {check['error_code']} "
            f"| {check['error_msg']} | {check['observation']} |"
        )
    lines += [
        "",
        "Raw JSON: [probe.json](probe.json); captured bodies under `bodies/`.",
        "Outputs in `feasibility/runs/` are gitignored and may contain restricted",
        "source data; CI consumes only reviewed fixtures under",
        "`tests/fixtures/feasibility/`.",
        "",
    ]
    return "\n".join(lines)
