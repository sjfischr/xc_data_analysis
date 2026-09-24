"""The RunSignup structured-extraction adapter (Task 8, design.md section 9.3).

Implements :class:`xc_platform.ingest.contract.SourceAdapter` using the
low-level client in ``runsignup_client.py``. Confirmed live findings this
module encodes (docs/runsignup-adapter-notes.md, Task 3.2):

* one RunSignup "race" per meet; race-ID-to-meet-number is static
  configuration, never derived from the payload;
* ``get-result-sets`` requires ``event_id``; ``get-results`` requires
  ``event_id`` alongside ``individual_result_set_id`` -- a result-set ID
  alone is not addressable, so discovery enumerates events first;
* custom fields are mapped by their accompanying header **label**, never by
  a hard-coded numeric field ID (the same "Team Name" label appeared under
  45 distinct IDs across the series);
* no total-count/page-count is published; completion is a terminal short
  page;
* frozen seasons (2023-2025) are discoverable/previewable but never fetched
  (Requirement 1.8, 4.9) -- the adapter itself stays season-agnostic and
  simply refuses to fetch anything in :data:`FROZEN_SEASONS`.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from xc_platform.ingest.adapters.runsignup_client import BASE, RunSignupClient
from xc_platform.ingest.contract import (
    DiscoveredResultSet,
    DiscoveryRequest,
    DiscoveryResult,
    FetchResult,
    FrozenSeasonRefusedError,
    NormalizedUrl,
    StagedResultInput,
)
from xc_platform.security.url_policy import RUNSIGNUP_FAMILY_HOSTS

SOURCE_NAMESPACE = "runsignup"

FROZEN_SEASONS: frozenset[int] = frozenset({2023, 2024, 2025})

DEFAULT_PER_PAGE = 100
DEFAULT_MAX_PAGES = (
    200  # safety ceiling; no total is published, so this bounds a runaway loop
)

# Race IDs recovered from the saved historical pages
# (docs/runsignup-adapter-notes.md). The NVJCYO series publishes one
# RunSignup "race" per meet, with every season's events nested inside that
# race; meet_number is therefore a property of the race ID, not of anything
# inside the payload.
MEET_RACE_IDS: dict[str, int] = {
    "154050": 1,
    "154708": 2,
    "155696": 3,
}

# Physical distance by division. Deliberately duplicated from
# xc_platform.migration.historical_csv.DIVISION_DISTANCE_METERS rather than
# imported: migration/ is a one-time package for the frozen historical
# backfill (see its docstring), and importing live-ingest code from it would
# point the dependency the wrong way. Both copies describe the same
# unchanging physical fact and are not expected to diverge.
DIVISION_DISTANCE_METERS: dict[str, int] = {
    "2nd Grade": 2000,
    "Frosh": 2000,
    "JV": 3000,
    "Varsity": 4000,
}

_DIVISION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\b2nd\b", "2nd Grade"),
    (r"\bfrosh\b", "Frosh"),
    (r"\bjv\b", "JV"),
    (r"\bvarsity\b", "Varsity"),
)

_GENDER_MAP: dict[str, str] = {
    "f": "F",
    "female": "F",
    "girl": "F",
    "girls": "F",
    "m": "M",
    "male": "M",
    "boy": "M",
    "boys": "M",
}

_RESULTS_URL_RE = re.compile(
    r"^https://(?P<host>[\w.-]+)/Race/(?:(?P<state>[A-Z]{2})/(?P<city>[^/]+)/)?"
    r"Results/(?P<race_id>\d+)",
    re.IGNORECASE,
)
_FRAGMENT_SET_RE = re.compile(r"resultSetId-(?P<set_id>\d+)", re.IGNORECASE)
_FRAGMENT_PERPAGE_RE = re.compile(r"perpage:(?P<per_page>\d+)", re.IGNORECASE)
_TIME_RE = re.compile(
    r"^(?:(?P<hours>\d+):)?(?P<minutes>\d{1,2}):(?P<seconds>\d{2}(?:\.\d+)?)$"
)

# Header labels observed for the same semantic field under different custom
# field IDs per result set (docs/runsignup-adapter-notes.md): "Year" and
# "Grade" both appear for grade; "Team Name" and "Team" both appear for
# school.
_GRADE_LABELS = frozenset({"year", "year (grade)", "grade"})
_TEAM_LABELS = frozenset({"team name", "team", "school", "group/team name"})
_SCORED_LABELS = frozenset({"scored"})


class UnsupportedRunSignupUrlError(ValueError):
    """The URL is not a recognized RunSignup(-family) results URL."""


def normalize_gender(value: str | None) -> str | None:
    if not value:
        return None
    return _GENDER_MAP.get(value.strip().lower())


def classify_event(name: str) -> tuple[str | None, str | None]:
    """Map an event name onto (division, gender), if recognizable."""
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


def season_year_from_start_time(start_time: str | None) -> int | None:
    if not start_time:
        return None
    found = re.search(r"\b(20\d{2})\b", start_time)
    return int(found.group(1)) if found else None


def normalize_runsignup_url(url: str) -> NormalizedUrl:
    """Normalize a RunSignup(-family) results URL, including fragment scope.

    Rewrites fragment scope (``#resultSetId-N;perpage:M``) into structured
    fields (design.md section 9.2) -- the fragment is never sent to the
    server, so a URL that carries scope only in its fragment is not
    independently reproducible without this client-side recovery (confirmed
    live, Task 3.3: query-string scope reached 20/20 name recall from Tavily
    Extract; fragment-only scope reached 0/20 on the identical page).
    """
    match = _RESULTS_URL_RE.match(url.strip())
    if not match:
        raise UnsupportedRunSignupUrlError(
            f"not a recognized RunSignup results URL: {url!r}"
        )
    host = match.group("host").lower()
    if not any(
        host == family or host.endswith(f".{family}")
        for family in RUNSIGNUP_FAMILY_HOSTS
    ):
        raise UnsupportedRunSignupUrlError(
            f"host {host!r} is not a RunSignup-family host"
        )

    fragment = url.partition("#")[2]
    set_match = _FRAGMENT_SET_RE.search(fragment)
    per_page_match = _FRAGMENT_PERPAGE_RE.search(fragment)
    return NormalizedUrl(
        original_url=url,
        source_namespace=SOURCE_NAMESPACE,
        race_id=match.group("race_id"),
        requested_result_set_id=int(set_match.group("set_id")) if set_match else None,
        requested_per_page=int(per_page_match.group("per_page"))
        if per_page_match
        else None,
    )


def _parse_time_to_ms(text: str | None) -> int | None:
    """Parse a ``[H:]MM:SS[.ss]`` clock/chip time string into milliseconds."""
    if not text:
        return None
    match = _TIME_RE.match(text.strip())
    if not match:
        return None
    hours = int(match.group("hours")) if match.group("hours") else 0
    minutes = int(match.group("minutes"))
    seconds = float(match.group("seconds"))
    total_seconds = hours * 3600 + minutes * 60 + seconds
    if total_seconds <= 0:
        return None
    return round(total_seconds * 1000)


def discover(client: RunSignupClient, request: DiscoveryRequest) -> DiscoveryResult:
    """Enumerate every event and result set published under one race ID.

    Frozen seasons are included and marked, not filtered out -- Task 8.2:
    "discoverable and previewable, but not importable." Only
    :func:`fetch` refuses them.
    """
    normalized = request.normalized_url
    race_id = normalized.race_id
    meta = client.get_json(
        f"{BASE}/race/{race_id}?format=json"
        "&future_events_only=F&most_recent_events_only=F",
        note=f"race {race_id} metadata",
    )
    race = meta.get("race") or {}
    events = race.get("events") or []
    meet_number = MEET_RACE_IDS.get(race_id, 0)

    race_info: dict[str, Any] = {
        "race_id": race_id,
        "meet_number": meet_number,
        "name": race.get("name"),
        "url": race.get("url"),
        "event_count": len(events),
    }

    result_sets: list[DiscoveredResultSet] = []
    for event in events:
        event_id_raw = event.get("event_id")
        if event_id_raw is None:
            continue
        event_id = int(event_id_raw)
        name = str(event.get("name") or "")
        year = season_year_from_start_time(event.get("start_time"))
        if year is None:
            continue
        division, gender = classify_event(name)
        distance_meters = DIVISION_DISTANCE_METERS.get(division) if division else None

        sets_payload = client.get_json(
            f"{BASE}/race/{race_id}/results/get-result-sets?format=json"
            f"&event_id={event_id}",
            note=f"result sets: {race_id}/{year}/{name}",
        )
        for block in sets_payload.get("individual_results_sets") or []:
            set_id_raw = block.get("individual_result_set_id")
            if set_id_raw is None:
                continue
            result_sets.append(
                DiscoveredResultSet(
                    source_namespace=normalized.source_namespace,
                    race_id=race_id,
                    event_id=event_id,
                    event_name=name,
                    set_id=int(set_id_raw),
                    set_name=str(block.get("individual_result_set_name") or ""),
                    season_year=year,
                    meet_number=meet_number,
                    division=division,
                    gender_code=gender,
                    distance_meters=distance_meters,
                    public_results=block.get("public_results") == "T",
                    preliminary_results=block.get("preliminary_results") == "T",
                    frozen_season=year in FROZEN_SEASONS,
                )
            )
    return DiscoveryResult(race_info=race_info, result_sets=tuple(result_sets))


def fetch(
    client: RunSignupClient,
    item: DiscoveredResultSet,
    *,
    per_page: int = DEFAULT_PER_PAGE,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> FetchResult:
    """Page through one result set until a terminal short/empty page.

    Raises :class:`~xc_platform.ingest.contract.FrozenSeasonRefusedError`
    for a frozen-season item (Requirement 1.8/4.9) -- discovery may surface
    it, but nothing ever fetches or stages it.
    """
    if item.frozen_season:
        raise FrozenSeasonRefusedError(
            f"season {item.season_year} is frozen (Requirement 1.8/4.9); "
            f"race {item.race_id} event {item.event_id} set {item.set_id} "
            "was discovered but will not be fetched"
        )

    rows: list[dict[str, Any]] = []
    headers: dict[str, str] = {}
    pages = 0
    for page in range(1, max_pages + 1):
        payload = client.get_json(
            f"{BASE}/race/{item.race_id}/results/get-results?format=json"
            f"&event_id={item.event_id}&individual_result_set_id={item.set_id}"
            f"&results_per_page={per_page}&page={page}",
            note=f"rows p{page}: {item.race_id}/{item.season_year}/{item.event_name}",
        )
        pages += 1
        sets = payload.get("individual_results_sets") or []
        if not sets:
            break
        block = sets[0]
        headers = headers or (block.get("results_headers") or {})
        page_rows = block.get("results") or []
        rows.extend(page_rows)
        if len(page_rows) < per_page:
            break

    content = json.dumps(rows, sort_keys=True, default=str).encode("utf-8")
    return FetchResult(
        item=item,
        raw_rows=tuple(rows),
        headers=headers,
        pages_fetched=pages,
        content_sha256=hashlib.sha256(content).hexdigest(),
        retrieved_at=datetime.now(UTC).isoformat(timespec="seconds"),
    )


def _invert_headers(headers: dict[str, str]) -> dict[str, str]:
    """``label (lowercased, trimmed) -> field key``, for by-label lookup.

    Restricted to ``custom-field-*`` keys. Confirmed live in the redacted
    contract fixture: a result set can publish *two* different fields under
    the identical label ("Scored") -- one a real ``custom-field-NNNNNN``
    business field, the other a ``division-NNNNNN-placement`` age/category
    placement flag that happens to reuse the same label text but is a
    structurally different concept and is frequently null. Restricting to
    ``custom-field-*`` avoids a same-label collision silently picking the
    wrong one.
    """
    return {
        label.strip().lower(): key
        for key, label in headers.items()
        if key.startswith("custom-field-")
    }


def _lookup_by_label(
    row: dict[str, Any], label_to_key: dict[str, str], labels: frozenset[str]
) -> Any:
    for label in labels:
        key = label_to_key.get(label)
        if key is not None and row.get(key) not in (None, ""):
            return row.get(key)
    return None


def normalize(payload: FetchResult) -> list[StagedResultInput]:
    """Map raw rows onto staging candidate fields, by header label (R5.3)."""
    label_to_key = _invert_headers(payload.headers)
    inputs: list[StagedResultInput] = []

    for row in payload.raw_rows:
        warnings: list[str] = []

        result_id = row.get("result_id")
        source_result_id = str(result_id) if result_id is not None else None

        first_name = str(row.get("first_name") or "").strip() or None
        last_name = str(row.get("last_name") or "").strip() or None
        if not first_name or not last_name:
            warnings.append("missing first_name or last_name")
        athlete_full_name = (
            " ".join(part for part in (first_name, last_name) if part) or None
        )

        team_raw = _lookup_by_label(row, label_to_key, _TEAM_LABELS)
        grade_raw = _lookup_by_label(row, label_to_key, _GRADE_LABELS)
        scored_raw = _lookup_by_label(row, label_to_key, _SCORED_LABELS)

        gender_code = normalize_gender(row.get("gender"))

        place_raw = row.get("place")
        try:
            place_overall = int(str(place_raw)) if place_raw not in (None, "") else None
        except (TypeError, ValueError):
            place_overall = None
            warnings.append(f"unparseable place: {place_raw!r}")

        try:
            grade = int(str(grade_raw)) if grade_raw not in (None, "") else None
        except (TypeError, ValueError):
            grade = None

        original_time_text = (
            str(row.get("chip_time") or "").strip()
            or str(row.get("clock_time") or "").strip()
            or None
        )
        finish_time_ms = _parse_time_to_ms(original_time_text)

        bib_raw = row.get("bib")
        bib = str(bib_raw) if bib_raw not in (None, "") else None

        candidate_fields: dict[str, Any] = {
            "season_year": payload.item.season_year,
            "meet_number": payload.item.meet_number,
            "division": payload.item.division,
            "gender_code": gender_code,
            "distance_meters": payload.item.distance_meters,
            "athlete_full_name": athlete_full_name,
            "team_name": str(team_raw).strip() if team_raw else None,
            "bib": bib,
            "grade": grade,
            "place_overall": place_overall,
            "finish_time_ms": finish_time_ms,
            "original_time_text": original_time_text,
            "scored_flag": "scored" if scored_raw == "*" else "unknown",
        }

        if source_result_id is not None:
            idempotency_key = f"runsignup:{source_result_id}"
        else:
            # No stable upstream identifier (Requirement 7.5): fall back to
            # a content hash of the raw row, and surface the collision risk.
            row_digest = hashlib.sha256(
                json.dumps(row, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
            idempotency_key = f"runsignup:contenthash:{row_digest}"
            warnings.append(
                "no stable result_id; idempotency key uses a content hash "
                "(Requirement 7.5) and may collide across identical rows"
            )

        inputs.append(
            StagedResultInput(
                source_result_id=source_result_id,
                idempotency_key=idempotency_key,
                raw_fields=dict(row),
                candidate_fields=candidate_fields,
                validation_warnings=tuple(warnings),
            )
        )
    return inputs


@dataclass
class RunSignupAdapter:
    """Implements :class:`~xc_platform.ingest.contract.SourceAdapter`."""

    client: RunSignupClient
    per_page: int = DEFAULT_PER_PAGE
    max_pages: int = DEFAULT_MAX_PAGES

    def can_handle(self, normalized_url: NormalizedUrl) -> bool:
        return normalized_url.source_namespace == SOURCE_NAMESPACE

    def discover(self, request: DiscoveryRequest) -> DiscoveryResult:
        return discover(self.client, request)

    def fetch(self, item: DiscoveredResultSet) -> FetchResult:
        return fetch(
            self.client, item, per_page=self.per_page, max_pages=self.max_pages
        )

    def normalize(self, payload: FetchResult) -> list[StagedResultInput]:
        return normalize(payload)
