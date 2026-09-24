# Tavily discovery and fallback notes (Task 3.3 evidence)

**Observed:** September 20, 2026. **Evidence:**
`feasibility/runs/<run-id>-tavily-discovery/`. Credit rates read from
[Tavily Credits & Pricing](https://docs.tavily.com/documentation/api-credits)
on the same date — never from memory.

The API key is resolved through
[`xc_platform.security.config.get_tavily_api_key`](../src/xc_platform/security/config.py)
and appears in no probe artifact.

## Account position (a live budget constraint)

At the start of the probe the account was on the **Researcher** plan:
**803 of 1,000 monthly credits already consumed**, leaving ~197. Pay-as-you-go
beyond the plan costs **USD 0.008/credit**, which lands directly on the USD 20
monthly ceiling. This is a real constraint on the design, not a hypothetical:
an unbounded crawl could exhaust the remaining allowance in one import.

## Credit rate card (fetched, not assumed)

| Operation | Cost |
|---|---|
| Search, basic | 1 credit per request |
| Search, advanced | 2 credits per request |
| Extract, basic | 1 credit per 5 successful URLs |
| Extract, advanced | 2 credits per 5 successful URLs |
| Map | 1 credit per 10 pages |
| Map with `instructions` | 2 credits per 10 pages |
| Crawl | map cost + extract cost |

Failed extractions and failed maps are not billed.

## `/usage` is not a metering mechanism

Two independent observations:

1. **Counters did not move.** `plan_usage` read 803 before a successful search,
   immediately after it, and on a later independent check — with
   `search_usage` unchanged at 527 throughout.
2. **The endpoint is rate-limited.** Polling it around individual operations
   produced HTTP 429, which persisted more than 75 seconds.

**Consequence for the design:** the platform must meter credits from its **own
request log** using the documented rate card, and treat `/usage` as an
occasional reconciliation check with backoff and graceful degradation — never
as a per-operation gate or a pre-flight budget check. The probe was rewritten
to work this way, and tolerates a 429 by recording the reading as unavailable.

## Gate F4 — discovery

**Search** (1 credit) returned 9–10 relevant results, of which 4 were
RunSignup-family and the rest other hosts. It surfaced the Meet 2 race
(`154708`) that the seed URL never references — useful for finding sibling
meets.

**Map** from the race results URL is weak: depth 1 returned 2 URLs, depth 2
with a 30-page limit returned 3, all within race `154050`
(`/Results/154050` and `/Results/154050/TeamResults`). It discovered **no other
race IDs, years, or divisions.**

Gate F4 passes on its second clause, not its first: Tavily Map cannot enumerate
the result-set structure, but **Task 3.2 proved REST can enumerate it
independently**. Therefore:

- **Map/Crawl is not a reliable enumerator for RunSignup.** Do not spend
  credits trying to make it one.
- **Search is genuinely useful for finding sibling races/meets** that a single
  submitted URL does not reference, and for locating non-RunSignup coverage.

## The query-parameter discovery (affects the adapter, not just Tavily)

Search surfaced a URL form the supplied spec URL does not use:
`https://runsignup.com/Race/Results/154708?resultSetId=499157`.

A controlled ablation, same page, same ground-truth names from REST:

| URL form | Extract depth | Content | Name recall |
|---|---|---|---|
| `?resultSetId=493637` | basic | 19,320 chars | **20/20** |
| bare `/Results/154050` | advanced | 1,928 chars | 0/20 |

**The query parameter is the determining factor, not extraction depth.**
RunSignup renders the selected result set server-side when scope arrives in the
query string; the fragment form (`#resultSetId-N`) renders client-side and is
invisible to any extractor or crawler.

Two consequences:

1. **URL normalization should rewrite fragment scope into query scope**
   (`#resultSetId-N;perpage:100` → `?resultSetId=N`). This makes a submitted URL
   addressable server-side, shareable, and extractable.
2. **Basic extraction depth is sufficient** when scope is in the query string —
   half the credit cost of advanced for identical recall on this source.

Latency figures from the ablation (0.062 s) almost certainly reflect Tavily
caching the page from the preceding call; **do not use them as production
latency estimates.** First-fetch latency measured 3.4 s.

## White-label mirror hosts

Search returned `www.trisignup.com/Race/Results/154050` and
`www.adventuresignup.com/Race/Results/154708` — **the same numeric race IDs
under different brands.** These are RunSignup white-label front ends.

The source router must treat `trisignup.com` and `adventuresignup.com` as
RunSignup-family hosts. If it does not, the same race will be ingested twice
under different provenance, creating duplicate meets and athletes that entity
resolution would then have to unpick. This is encoded as
`RUNSIGNUP_FAMILY_HOSTS` with a contract test.

## Gate F5 — non-RunSignup fallback: NOT DEMONSTRATED

Tavily Extract is proven against **RunSignup-family** results pages (20/20
recall above). That is the easy case, and it is also the case the design says
to route back to REST anyway.

The one genuine non-RunSignup source tested — the MileSplit NVJCYO team page —
returned **296 characters** with no time patterns and no athlete rows. It is a
team landing page, not a results table, and quite possibly gated.

Search also surfaced `directathletics.com`, `windsorrunning.com`, and
`blueridgetiming.com`, none of which were probed.

**Therefore F5 remains UNVERIFIED.** Per Task 3.3's own wording this is a
permitted outcome, recorded explicitly rather than papered over. Consequences:

- Non-RunSignup fallback must ship **experimental**, requiring manual upload
  and review, until a specific source is approved and measured.
- No owner has approved a fallback domain. Domain approval is an explicit
  administrative act (exact-domain allowlist), and no probe should add one.

## Recommended production limits

| Limit | Value | Basis |
|---|---|---|
| Max pages per run | 25 | Map returned ≤3 useful pages; 25 is generous headroom |
| Max crawl depth | 2 | Depth 2 added one URL over depth 1 — deeper is waste |
| Max wall-clock per run | 120 s | Slowest observed operation 3.4 s |
| Max response bytes | 8 MiB | Session cap; largest observed 19 KB |
| Max credits per import | 10 | ~5% of the remaining monthly allowance |
| Extraction depth | basic | Identical recall at half cost |

Credit exhaustion must **pause** a run in a resumable state, never fail it
silently and never spill into pay-as-you-go without an explicit decision.

## Content safety

No prompt-injection signal was found in any content fetched during the probe.
The scanner (`scan_for_injection`) records signals without ever acting on
fetched instructions, and is covered by contract tests. All fetched content
remains untrusted data.

## Redaction note

Two discovered RunSignup URLs were written to the probe artifact as
`https://runsignup.***REDACTED***`. This is the redaction layer being
conservative about token-shaped path segments. It is the safe direction of
error, but it means probe artifacts can under-report discovered URLs; the raw
bodies under `bodies/` remain available for review.
