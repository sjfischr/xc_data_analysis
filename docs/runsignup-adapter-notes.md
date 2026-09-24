# RunSignup adapter notes (Task 3.2 evidence)

**Observed:** September 20, 2026, against live public endpoints.
**Evidence:** `feasibility/runs/<run-id>-runsignup-coverage/` (gitignored raw
bodies); redacted contract fixture in
[tests/fixtures/feasibility/runsignup_get_results.json](../tests/fixtures/feasibility/runsignup_get_results.json).

These are adapter implementation details, not permanent domain contracts
(design §3.2). Re-verify before relying on them in a later release.

## Series topology

The NVJCYO series publishes **one RunSignup "race" per meet**, with every
season's events nested inside that race. Race IDs were recovered from the
saved historical pages under `data/pages/`:

| Race ID | Meet | Race name | Seasons observed |
|---|---|---|---|
| 154050 | 1 | NVJCYO Cross Country Developmental Meet 1 | 2023, 2024, 2025, 2026 |
| 154708 | 2 | NVJCYO Cross Country Developmental Meet 2 | 2023, 2024, 2025 |
| 155696 | 3 | NYJCYO Cross Country Championship | 2023, 2024, 2025 |

Meet number is therefore a property of the race ID, not of anything inside the
payload. The adapter must carry an explicit race-ID-to-meet mapping and treat
it as configuration, not as a derived value.

## Endpoints used

All responses are JSON when `format=json` is supplied.

| Purpose | Endpoint | Required parameters |
|---|---|---|
| Race + event metadata | `GET /Rest/race/{race_id}` | `format`, `future_events_only=F`, `most_recent_events_only=F` |
| Result-set enumeration | `GET /Rest/race/{race_id}/results/get-result-sets` | `format`, **`event_id`** |
| Paginated rows | `GET /Rest/race/{race_id}/results/get-results` | `format`, `event_id`, `individual_result_set_id`, `results_per_page`, `page` |

`future_events_only=F` and `most_recent_events_only=F` are both required to see
historical seasons; the defaults hide them.

`get-result-sets` **requires `event_id`** — a race-wide call returns
`error_code 3, "Invalid parameters"`. Likewise `get-results` cannot be driven by
`individual_result_set_id` alone; `event_id` must accompany it. This matters
because the supplied URL supplies only a result-set ID, so the adapter must
first enumerate events to find the event that owns that set.

## Authentication and terms

- Every endpoint above returned athlete-level data **without any API key**, for
  result sets whose `public_results` flag is `"T"`.
- RunSignup documents API keys and OAuth2 for partner/registration endpoints.
  Nothing in the read-only public-results path required them during this probe.
- **Documented rate limit:** "no more than 2 concurrent API calls at a time."
  The probe ran strictly sequentially with 250 ms spacing: 152 requests, all
  HTTP 200, 15.6 s total, slowest single request 0.172 s. No 429 was observed.
- The production adapter must stay within that concurrency guidance, keep the
  identifying `User-Agent`, and treat the terms as subject to change. No burst
  or concurrency-limit test was run — deliberately, to avoid behaving like an
  abusive client against a third party.

## Response contract

Result-set blocks carry `individual_result_set_id`, `individual_result_set_name`,
`public_results`, `preliminary_results`, `results_source_name`,
`results_source_url`, and `pace_type`. Row objects carry stable
`result_id`, `place`, `bib`, `first_name`, `last_name`, `gender`, `clock_time`,
`chip_time`, and `pace`.

**Custom fields are per-result-set.** The same header label appears under many
different numeric IDs across the series:

| Header label | Distinct numeric field IDs observed |
|---|---|
| Team Name | 45 |
| Year (grade) | 42 |
| Team Score | 41 |
| Team Place | 27 |
| Score | 26 |
| Scored | 17 |
| Grade | 16 |
| RD Pace | 16 |
| Gender Place | 4 |

This is direct evidence for the design rule (§9.3): **map custom fields by
their header label, never by a hard-coded numeric ID.** A `custom-field-457581`
that means "Team Name" in the 2024 Varsity Girls set means nothing in another.

`city`, `state`, and `country_code` are present but null throughout this series.
They are outside the normalization allowlist (§15.3) and must not be carried
into canonical results regardless.

## Pagination and completion

No total-count, page-count, or "has more" field is published. Verified on set
`691274` (121 rows) at 25 rows/page:

- 5 pages returned `[25, 25, 25, 25, 21]`;
- zero duplicate and zero missing `result_id`s versus a single-page reference
  fetch;
- ordering identical to the reference fetch;
- page 999 returned an empty row list rather than an error.

**Completion rule for the adapter:** a terminal short page (fewer rows than
`results_per_page`), corroborated by unique `result_id` accounting. Never infer
completion from a row count alone.

## Error behavior

Errors arrive as **HTTP 200 with an error envelope**, not as HTTP status codes:

| Case | HTTP | `error_code` | Message |
|---|---|---|---|
| `get-result-sets` without `event_id` | 200 | 3 | Invalid parameters |
| Non-existent event/result-set pair | 200 | 301 | Event not found. |
| Non-existent race ID | 200 | 201 | Race not found. |

The adapter must therefore inspect the body for an `error` key on **every**
response and must not treat HTTP 200 as success. A retry policy keyed only on
status codes would loop forever on a permanent error.

## Adapter risks

1. **Fragment-only scope.** The supplied URL carries its selection in the hash
   fragment (`#resultSetId-691534;perpage:100`), which browsers never transmit.
   Scope must be parsed client-side and translated into REST parameters.
2. **Event-to-set indirection.** A result-set ID alone is not addressable; the
   adapter must enumerate events to locate its owner, costing one request per
   event on first discovery. Cache the mapping per race.
3. **Undocumented endpoint stability.** These endpoints are not contractually
   guaranteed. The recorded fixture limits blast radius; a contract test should
   fail loudly rather than silently normalizing a changed payload.
4. **Error-envelope masking.** See above — HTTP 200 does not imply data.
5. **Preliminary results.** `preliminary_results` is published per set. Rows
   from a preliminary set must be marked and must not silently overwrite final
   results.
6. **Team-only sets are legitimate.** A published set with zero rows is a real
   source state (2023 Meet 2), not a fetch failure. Quarantine and report it;
   never retry it into existence or synthesize rows.
