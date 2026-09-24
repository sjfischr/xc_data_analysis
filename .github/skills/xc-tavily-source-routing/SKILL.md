---
name: xc-tavily-source-routing
description: 'Use when routing XC results ingestion between the RunSignup REST API and Tavily Map/Crawl discovery: source priority rules, provenance labeling, URL-driven intake, and RunSignup-family host detection.'
---

# Source routing: RunSignup only for release 1, Tavily for discovery

> Status: **design policy** (normative), and the discovery-only scope below
> is **confirmed live** (Task 3.3, 2026-09-20) and **decided by the owner**
> (2026-09-21).
> Docs checked 2026-09-20: [Tavily documentation](https://docs.tavily.com/),
> [RunSignup API](https://runsignup.com/API).

> **Amended 2026-09-21 by owner decision: non-RunSignup extraction fallback is
> dropped from release 1 entirely**, not merely shipped experimental. Task 3.3
> found no genuine non-RunSignup source producing usable rows (the one tested,
> a MileSplit team page, returned no results table). Release 1 ingests
> RunSignup-family results only. The former "fallback ingestion" rule below is
> struck through and kept as a record for a future release that specifically
> approves and measures a source.

## Routing rules (in order)

1. **RunSignup REST API is the only ingestion source in release 1.**
   Structured JSON beats extraction every time. Sample shape the platform
   must normalize:
   `https://runsignup.com/Race/Results/154050#resultSetId-691534;perpage:100`
   (race → result set → paginated results).
2. **Tavily discovery only**, on approved (RunSignup-family) domains: find
   results pages, series pages, and schedules. Discovery output feeds the
   URL intake queue — it never directly creates results rows.
   **Search, not Map/Crawl, is the sibling-discovery mechanism** (confirmed
   live, Task 3.3, and propagated into Requirement 6.1 on 2026-09-22): Map
   found only 2-3 pages within the seed URL's own race ID at every tested
   depth and discovered zero sibling race IDs/years/divisions -- "not a
   reliable enumerator for RunSignup." Search against the same seed
   surfaced a sibling meet (race 154708) the seed URL never referenced.
   Map/Crawl remains available for exploring structure within an
   already-known domain; do not spend credits on it expecting enumeration.
3. ~~**Tavily Extract/Crawl content = fallback ingestion** only when no
   structured connector exists for the source, and every row it produces is
   provenance-labeled (`source_kind = web_extract`, source URL, fetched-at).~~
   *(Removed 2026-09-21 — see amendment above.)*
4. If Tavily discovers a RunSignup page, **route back to the REST API** for
   the actual data; do not extract what the API can serve.
5. **Treat `trisignup.com` and `adventuresignup.com` as RunSignup-family
   hosts, not distinct sources.** Confirmed live (Task 3.3, 2026-09-20): both
   are white-label front ends serving the same numeric race IDs as
   `runsignup.com`. Routing them separately ingests the same race twice under
   different provenance, manufacturing duplicate meets and athletes.
6. **Rewrite fragment scope into query-string scope during URL
   normalization**: `#resultSetId-N;perpage:M` -> `?resultSetId=N`. Confirmed
   live: a fragment-only URL scored 0/20 name recall from Tavily Extract on
   the identical page that scored 20/20 once scope moved to the query string
   -- the fragment is never sent to the server, so it is invisible to any
   extractor or crawler, not just to the REST adapter.
7. **Any URL that does not resolve to a RunSignup-family host is refused**,
   not routed to an extraction adapter — there is no non-RunSignup adapter in
   release 1.

## Provenance requirements (every fetched payload)

- Store the raw payload immutably (`raw/<source>/<yyyy>/<sha256>.json.gz`)
  before normalization; keep `source_url`, `fetched_at`, request ID, and
  content hash. This still applies to Tavily discovery responses even though
  they no longer produce result rows directly.
- Missing required fields → store `null` and **quarantine the row** for
  review; never guess or synthesize values (this is youth race data — a
  wrong result is worse than a missing one).

## URL-driven ingestion contract

The admin submits a URL; the pipeline classifies it (RunSignup-family race or
rejected) and reports which route it took and why in the run log. A
non-RunSignup-family URL is refused outright with an explanation — there is no
"unknown source, spend credits to find out" path in release 1.

## Anti-patterns

- ~~Extracting a page that the RunSignup API already serves as JSON.~~ Moot in
  release 1 — there is no extraction path at all.
- Routing a non-RunSignup-family URL to any adapter instead of refusing it.
- Letting discovery output bypass the intake queue and write rows directly.
- Normalizing away source disagreement — conflicting sources create
  resolution cases, not silent overwrites.
- Treating `trisignup.com` / `adventuresignup.com` as a different source from
  `runsignup.com`.
