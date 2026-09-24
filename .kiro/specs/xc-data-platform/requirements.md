# XC Data Platform Modernization — Requirements

**Status:** Approved September 19, 2026  
**Workflow:** Requirements-first feature spec  
**Last updated:** September 19, 2026

## 1. Purpose

Modernize the existing NVJCYO cross-country analytics application from a manually maintained CSV pipeline and Streamlit dashboard into a durable, agent-enabled data platform.

The current tool converts saved RunSignup HTML/MHTML pages into a flat season-results CSV, cleans naming inconsistencies through sequential scripts, computes normalized pace and cross-country scoring, and presents athlete, team, and Saint Sebastian Award analytics. It is valuable because it turns inconsistent public race results into longitudinal athlete and team insights that RunSignup does not provide directly. Its current architecture is difficult to extend because data identity is string-based, scripts destructively rewrite one CSV, ingestion is manual, analytics are tightly coupled to Streamlit, and Heroku does not fit the desired agent-runtime model.

The target platform shall preserve the existing analytical value while adding normalized SQLite storage with S3 durability, URL-driven hybrid ingestion, auditable entity resolution, advanced analytics, rich visualizations, Strands agents hosted through Amazon Bedrock AgentCore, and an invite-only AWS-hosted user experience for the owner and a small group of coaches and parents.

## 2. Goals

1. Replace the flat CSV as the operational data backend with a normalized, migration-managed SQLite database.
2. Use Amazon S3 as the durable, versioned store for database artifacts and recovery data without treating S3 as a live POSIX filesystem.
3. Import complete race-result data from a submitted URL through a hybrid RunSignup REST and Tavily workflow.
4. Deduplicate meets, schools, athletes, races, and results with deterministic rules first, agent assistance second, and human approval for ambiguous cases.
5. Add trustworthy advanced analytics, natural-language exploration, and declarative chart generation.
6. Build agents with Strands Agents and deploy them to Amazon Bedrock AgentCore Runtime with appropriate short- and long-term memory.
7. Replace Streamlit with a richer, responsive interface capable of rendering sanitized Markdown, accessible tables, and interactive charts.
8. Migrate hosting from Heroku to AWS using request-driven services. *(Amended 2026-09-20: the USD 20 spend goal was removed by owner decision.)*
9. Encode reusable implementation guidance as repository-local agent skills (amended 2026-09-20; see Requirement 14).

## 3. Non-goals

1. The first release is not a registration, timing, payment, or race-management system.
2. The platform will not infer or fabricate missing race results when a source does not publish them.
3. The platform will not scrape authenticated or private athlete data.
4. The first release does not require multi-writer database concurrency or offline-first editing.
5. The first release does not require a public, anonymous dashboard.
6. The first release will not train a custom machine-learning model or foundation model.
7. Agent memory will not replace the relational database as the authoritative record for identity decisions or race data.

## 4. Users and roles

- **Administrator:** The application owner. Can submit imports, inspect ingest runs, adjudicate entity matches, manage aliases, restore database versions, and use all analytics.
- **Viewer:** An invited coach or parent. Can browse approved data and use permitted analytical features but cannot import data, alter identities, publish a database version, or administer users.
- **System agent:** A constrained Strands agent acting through explicit tools and the authenticated user's permissions. It has no implicit database-write authority.

## 5. Glossary

- **Meet:** A dated event in a season, such as Developmental Meet 1 or the Championship.
- **Race:** One division and gender result set within a meet.
- **Result:** One athlete's recorded finish in one race.
- **Source result ID:** A stable identifier supplied by an upstream system, such as RunSignup `result_id`.
- **Alias:** A source spelling or representation linked to one canonical meet, school, or athlete.
- **Resolution:** The decision to match a staged source entity to an existing canonical entity or create a new one.
- **Ingest run:** One auditable execution of URL discovery, extraction, validation, resolution, and commit.
- **Live database:** The local SQLite copy opened by an application or writer process.
- **Durable database artifact:** A checksummed, versioned SQLite snapshot and associated recovery data stored in S3.
- **Saint Sebastian standings:** Cumulative-time standings by season, division, and gender for athletes satisfying the configured participation rule.

## 6. Assumptions requiring confirmation during design

1. Initial usage is no more than 10 monthly active users, 500 dashboard page loads, 100 agent prompts, and three full meet imports per month during the active season.
2. Imported results are public race records. The platform needs athlete name, school/team, grade or division, gender category, bib, place, time, source IDs, and meet metadata, but does not need date of birth, home address, phone, email, or other registration data.
3. A single administrator is an acceptable database writer for the first release.
4. RunSignup's public REST endpoints may be used subject to feasibility verification of availability, stability, rate limits, and terms.
5. The currently committed historical dataset is the parity baseline and is **frozen**. *(Amended 2026-09-20: it is no longer superseded by better authoritative source records; live-source differences for 2023–2025 are recorded as observations only.)*

## 7. Functional requirements

### Requirement 1 — Preserve existing analytical behavior

*(Amended 2026-09-20 by owner decision: the 2023, 2024, and 2025 seasons are
**frozen**. They are migrated exactly as the baseline holds them and are not
reconciled against, or corrected from, the live source. New ingestion targets
the 2026 season only. See criteria 8–10.)*

**User story:** As the administrator, I want the modernized platform to preserve trusted historical calculations so that modernization does not silently change results.

#### Acceptance criteria

1. WHEN the historical migration runs, THE SYSTEM SHALL import every valid row in the selected baseline dataset exactly once or place it in a documented quarantine report.
2. WHEN migration validation runs, THE SYSTEM SHALL compare row counts, unique athletes, schools, seasons, meets, divisions, genders, and results by season/meet/division/gender against a frozen baseline.
3. WHEN normalized pace is calculated, THE SYSTEM SHALL reproduce the established distance-normalized pace and speed outputs for equivalent source inputs.
4. WHEN team scoring is calculated, THE SYSTEM SHALL sum the places of each eligible team's first five finishers within one race, exclude teams with fewer than five finishers, and rank lower scores ahead of higher scores.
5. WHEN Saint Sebastian standings are calculated, THE SYSTEM SHALL reproduce the configured cumulative-time, participation, rank, and time-back rules for equivalent historical inputs.
6. IF any required parity assertion fails, THEN THE SYSTEM SHALL block production cutover and identify the differing records and calculations.
7. IF source data is absent, incomplete, or team-only, THEN THE SYSTEM SHALL represent the gap explicitly and SHALL NOT infer athlete-level results.
8. THE 2023, 2024, and 2025 seasons SHALL be treated as frozen historical data: migrated exactly as the frozen baseline holds them, with the baseline itself as the parity target.
9. THE SYSTEM SHALL NOT correct, backfill, or overwrite frozen historical seasons from the live source, including where the live source now publishes more or fewer rows than the baseline. Such differences SHALL be recorded as documented observations only.
10. THE documented 2023 Meet 2 athlete-level gap SHALL remain a gap and SHALL NOT be filled from any source.

### Requirement 2 — Normalized SQLite data backend

**User story:** As the administrator, I want canonical entities and transactional storage so that data can be updated safely and analyzed consistently.

#### Acceptance criteria

1. THE SYSTEM SHALL use SQLite as the operational relational database for meets, races, schools, athletes, athlete-season associations, results, aliases, ingest runs, staged records, and resolution decisions.
2. THE SYSTEM SHALL assign internal immutable identifiers to canonical meets, races, schools, and athletes rather than using display names as primary keys.
3. THE SYSTEM SHALL retain upstream source identifiers and SHALL enforce uniqueness for a source result within its source namespace.
4. THE SYSTEM SHALL enforce foreign-key, required-field, uniqueness, and domain constraints appropriate to each entity.
5. THE SYSTEM SHALL manage schema changes through ordered, repeatable migrations that can initialize an empty database and detect incompatible versions.
6. THE SYSTEM SHALL store source measurements needed for audit and SHALL derive reproducible metrics through versioned queries or views rather than destructive post-processing scripts.
7. WHEN a transaction fails validation, THE SYSTEM SHALL roll back all changes from that transaction.
8. WHEN historical aliases and curated corrections are migrated, THE SYSTEM SHALL retain their provenance and prior human decisions.

### Requirement 3 — SQLite durability in Amazon S3

**User story:** As the administrator, I want the small SQLite database durably protected in S3 so that it cannot be corrupted or lost.

#### Acceptance criteria

1. THE SYSTEM SHALL open and modify SQLite only on a filesystem that satisfies SQLite locking and atomic-write requirements.
2. THE SYSTEM SHALL NOT open a writable SQLite database directly through an S3 filesystem mount or treat an S3 object as a live random-access database file.
3. WHEN an application instance starts, THE SYSTEM SHALL obtain an approved database version, download it to local storage, and verify its checksum before opening it.
4. WHEN a writer publishes a committed database, THE SYSTEM SHALL acquire a single-writer lock, produce a consistent snapshot, upload it atomically, record its checksum and schema version, and release the lock.
5. IF another valid writer holds the lock, THEN THE SYSTEM SHALL reject or queue the second write without allowing concurrent publication.
6. THE SYSTEM SHALL enable S3 object versioning for database snapshots and SHALL retain sufficient metadata to identify the active version.
7. WHEN a reader refreshes, THE SYSTEM SHALL switch between complete verified snapshots without observing a partially uploaded database.
8. WHEN the administrator selects a prior valid version, THE SYSTEM SHALL restore it through a documented and tested procedure.
9. IF upload, checksum validation, or publication fails, THEN THE SYSTEM SHALL preserve the previously active version and report the failure.
10. THE SYSTEM SHALL document recovery-point, recovery-time, retention, and single-writer limitations validated by the feasibility analysis.

### Requirement 4 — URL-based intake experience

*(Amended 2026-09-20 by owner decision: live intake targets the **2026 season
only**. The adapter remains season-agnostic, but an import whose scope resolves
to a frozen season must be refused rather than executed — see criterion 9.)*

**User story:** As the administrator, I want to paste a race-results URL and import the available result data without manually saving pages or naming files.

#### Acceptance criteria

1. WHEN the administrator submits a supported HTTPS race-results URL, THE SYSTEM SHALL validate and normalize the URL, create an ingest run, and display its progress and selected extraction strategy.
2. WHEN a RunSignup URL contains a race ID, year, result-set query, or browser fragment such as `#resultSetId-...`, THE SYSTEM SHALL preserve and interpret that scope.
3. WHEN the submitted URL identifies a meet or race collection, THE SYSTEM SHALL discover all public result sets within the administrator-approved scope rather than importing only the initially visible browser page.
4. BEFORE committing an ingest run, THE SYSTEM SHALL show source metadata, discovered races, staged row counts, validation warnings, proposed entity resolutions, and records requiring review.
5. WHEN an approved ingest run is committed, THE SYSTEM SHALL update all valid records in one transaction and publish a new durable database version.
6. WHEN the same unchanged source is imported more than once, THE SYSTEM SHALL produce no duplicate entities or results and SHALL report inserted, updated, unchanged, and quarantined counts.
7. IF the URL is unsupported, inaccessible, private, or contains no extractable results, THEN THE SYSTEM SHALL fail safely with an actionable explanation and no production data changes.
8. THE SYSTEM SHALL restrict outbound intake requests to approved protocols and public network destinations and SHALL revalidate redirects to prevent server-side request forgery.
9. IF a submitted URL's resolved scope targets a frozen historical season (2023, 2024, or 2025), THEN THE SYSTEM SHALL refuse the import, explain that the season is frozen, and make no staging or canonical change. *(Added 2026-09-20.)*

### Requirement 5 — RunSignup structured extraction path

**User story:** As the administrator, I want RunSignup data acquired through its structured public interfaces so that imports are complete, stable, and less dependent on page markup.

#### Acceptance criteria

1. WHEN the source is a supported RunSignup results URL, THE SYSTEM SHALL prefer the verified RunSignup REST extraction path over HTML table parsing.
2. THE SYSTEM SHALL discover race metadata, event identifiers, public result sets, source-provided distances, result headers, and paginated results required for the approved import scope.
3. THE SYSTEM SHALL map separate first and last names, source result IDs, bib, place, clock/chip time, pace, gender category, school/team, grade/year, and scored flags when the source provides them.
4. THE SYSTEM SHALL preserve the raw source payload or an immutable content-addressed reference sufficient to audit field mapping later.
5. WHEN a result page contains more rows than one response page, THE SYSTEM SHALL retrieve every page exactly once and verify completion against available source totals or pagination signals.
6. IF a request encounters a transient failure or rate limit, THEN THE SYSTEM SHALL retry with bounded exponential backoff and SHALL stop before exceeding configured request and time budgets.
7. IF the structured path is unavailable or incomplete, THEN THE SYSTEM SHALL record the reason and represent the gap explicitly rather than silently changing extraction strategy. *(Amended 2026-09-21: the Tavily extraction-fallback mechanism this criterion referenced was dropped from release 1 by owner decision — see Requirement 6. A RunSignup structured-path gap is now reported as a documented gap, never filled by any extraction path.)*

### Requirement 6 — Tavily discovery

*(Amended 2026-09-21 by owner decision: non-RunSignup extraction fallback is
dropped from release 1 entirely, not merely shipped experimental. Task 3.3
found no genuine non-RunSignup source that produced usable rows — the one
candidate tested, a MileSplit team page, returned no results table. Release 1
supports RunSignup-family sources only (including white-label mirrors,
R4/R8.1); Tavily's role narrows to discovery. Former criterion 2, extraction
fallback, is removed. A future release may reintroduce it against a
specifically approved and measured source.)*

*(Amended 2026-09-22: criterion 1 was written before Task 3.3's live
evidence existed and assumed Map/Crawl would enumerate a race's structure.
Measured result (gate F4, `docs/tavily-routing-notes.md`): Map found only
2-3 pages within the submitted race's own ID and discovered no sibling race
IDs, years, or divisions at any tested depth — "Map/Crawl is not a reliable
enumerator for RunSignup." Search, by contrast, surfaced a sibling meet
(race 154708) the submitted URL never referenced. Criterion 1 now names
Search as the mechanism for what Requirement 6's user story actually asks
for -- finding sibling races and years a submitted URL doesn't reference --
and keeps Map/Crawl available for exploring page structure within an
already-known domain, not as the enumeration mechanism.)*

**User story:** As the administrator, I want Tavily to discover related RunSignup result pages so that a submitted URL's sibling races and years can be found without manual browsing.

#### Acceptance criteria

1. WHEN source discovery is requested, THE SYSTEM SHALL use Tavily Search to find sibling race, season, event, and result-set pages the submitted URL does not itself reference, within the configured domain and scope, and MAY use Map or Crawl to explore page structure within an already-known domain. *(Amended 2026-09-22: Map/Crawl is not a reliable enumerator for RunSignup — see amendment above.)*
2. *(Removed 2026-09-21: non-RunSignup extraction fallback is out of release-1 scope. See amendment above.)*
3. WHEN Tavily discovers RunSignup pages, THE SYSTEM SHALL route result retrieval through the structured RunSignup connector when feasible rather than treating generated page text as authoritative result rows.
4. THE SYSTEM SHALL constrain Tavily search result count, crawl instructions, depth, page count, domains, execution time, and credit use through administrator-configurable limits.
5. *(Removed 2026-09-21: this criterion governed quarantining extracted result fields, which no longer exist without extraction fallback. See amendment above.)*
6. THE SYSTEM SHALL treat fetched web content as untrusted data and SHALL NOT execute or follow instructions embedded in that content.
7. THE SYSTEM SHALL record Tavily request identifiers, URLs, timestamps, strategies, and credit usage without recording the API key.
8. IF the configured Tavily page or request limit for a run is reached, THEN THE SYSTEM SHALL stop additional Tavily calls and provide a resumable status. *(Amended 2026-09-20: credit-budget wording replaced with a runaway-protection limit.)*

### Requirement 7 — Staging, validation, provenance, and idempotency

**User story:** As the administrator, I want every import to be inspectable and reversible so that automation cannot silently corrupt trusted race data.

#### Acceptance criteria

1. WHEN source records are extracted, THE SYSTEM SHALL write them to staging with ingest-run, source URL, retrieval time, source identifier, content hash, and raw-field provenance before changing canonical entities.
2. BEFORE resolution, THE SYSTEM SHALL validate required fields, supported times, places, divisions, gender categories, distances, and internal source consistency.
3. WHEN a field is transformed, THE SYSTEM SHALL preserve the original value and identify the transformation or mapping used.
4. WHEN a source record has a stable upstream identifier, THE SYSTEM SHALL use the source namespace and identifier as its primary idempotency key.
5. WHEN no stable upstream identifier exists, THE SYSTEM SHALL use a documented composite and content-hash strategy and SHALL surface potential collisions.
6. IF a staged record conflicts with an existing result, THEN THE SYSTEM SHALL preserve both source versions in the audit trail and require a deterministic rule or human decision before destructive replacement.
7. WHEN an ingest run completes or fails, THE SYSTEM SHALL retain status, counts, warnings, errors, duration, and resulting database version.
8. WHEN an ingest run is rolled back, THE SYSTEM SHALL return canonical data to the prior version without deleting the audit record.

### Requirement 8 — Meet, school, and athlete resolution

**User story:** As the administrator, I want assisted entity resolution that recognizes known aliases without merging siblings or unrelated athletes.

#### Acceptance criteria

1. WHEN resolving a meet, school, or athlete, THE SYSTEM SHALL apply exact source-ID, canonical-key, and approved-alias rules before invoking an agent.
2. WHEN deterministic matching produces one valid match, THE SYSTEM SHALL use it and record the rule and confidence.
3. WHEN deterministic matching is inconclusive, THE SYSTEM SHALL provide the resolution agent only the relevant candidate context, including source spelling, school, season, grade progression, gender category, bib history, race history, and prior approved aliases when available.
4. THE SYSTEM SHALL use separate configurable thresholds for automatic match, automatic create, and human review.
5. IF candidate evidence conflicts or confidence falls in the review range, THEN THE SYSTEM SHALL create an approval-queue item and SHALL NOT merge the entities automatically.
6. WHEN the administrator approves, rejects, splits, or reverses a resolution, THE SYSTEM SHALL preserve the decision, actor, timestamp, evidence summary, and affected records.
7. WHEN an approved alias recurs, THE SYSTEM SHALL reuse the authoritative database decision without requiring another model call.
8. THE SYSTEM SHALL treat the SQLite alias and decision records, not conversational memory, as the authoritative resolution history.
9. WHEN evaluated against the curated duplicate-name evidence, THE SYSTEM SHALL honor all existing `apply` corrections, keep all confirmed-distinct pairs separate, and route all previously identified ambiguous pairs to review unless new authoritative evidence resolves them.
10. THE SYSTEM SHALL support correction of a mistaken merge without loss of original source values or result provenance.

### Requirement 9 — Analytical query services

**User story:** As a coach or parent, I want trustworthy longitudinal and comparative analytics so that I can understand athlete and team performance beyond a single results page.

#### Acceptance criteria

1. THE SYSTEM SHALL provide the existing athlete progression, season overview, normalized fastest pace, top placement, improvement, team scoring, and Saint Sebastian analyses through stable query interfaces.
2. THE SYSTEM SHALL provide head-to-head athlete comparison, percentile/performance-tier analysis, statistically qualified improvement or breakout analysis, and what-if team scoring.
3. WHEN comparing races with different distances, THE SYSTEM SHALL use source-supported distance normalization and clearly label the comparison method.
4. WHEN sample size is insufficient for a requested calculation, THE SYSTEM SHALL state the limitation and SHALL NOT present an unsupported ranking or trend as conclusive.
5. WHEN an analytical method depends on configurable business rules, THE SYSTEM SHALL display or link the active rule version.
6. WHEN a query applies season, meet, school, division, gender, grade, or athlete filters, THE SYSTEM SHALL apply those filters consistently to metrics, tables, and charts.
7. THE SYSTEM SHALL expose machine-readable result sets suitable for both the web UI and agent tools.
8. THE SYSTEM SHALL support export of the displayed tabular result set in a common non-proprietary format without exposing hidden or unauthorized fields.

### Requirement 10 — Strands analytics agents and tools

**User story:** As an authenticated user, I want to ask natural-language questions and receive answers grounded in approved platform data.

#### Acceptance criteria

1. THE SYSTEM SHALL implement analytical and resolution agents with Strands Agents and explicit, typed tools.
2. THE SYSTEM SHALL provide read-only tools for schema discovery, controlled analytical queries, entity lookup, approved metric computation, and declarative chart creation.
3. THE SYSTEM SHALL reject agent-generated database writes except through separately authorized, validated workflow tools available only to the administrator.
4. THE SYSTEM SHALL constrain tool calls by user role, row limits, statement type, execution time, iteration count, and per-request token limit. *(Amended 2026-09-20: monthly cost budget removed.)*
5. WHEN an agent presents a factual analytical answer, THE SYSTEM SHALL provide the active filters, data version, and sufficient query or method provenance for a user to verify the result.
6. IF a tool fails or returns no supporting data, THEN THE SYSTEM SHALL describe the limitation and SHALL NOT fabricate an answer.
7. WHEN a user requests a chart, THE SYSTEM SHALL return a validated declarative chart specification and its underlying bounded data rather than executable client code.
8. THE SYSTEM SHALL sanitize agent-produced Markdown and SHALL NOT execute agent-produced HTML, JavaScript, SQL, shell commands, or fetched instructions in the browser.

### Requirement 11 — AgentCore Runtime and memory

*(Amended 2026-09-22 by owner decision: production AgentCore Runtimes target
**platform version V2** for consistent cold starts and lower idle cost —
see criterion 11 and design.md section 12.6.)*

**User story:** As the administrator, I want production agents to use a managed runtime and useful memory while retaining security and portability controls.

#### Acceptance criteria

1. SUBJECT TO the approved feasibility gate, THE SYSTEM SHALL deploy production Strands agents to Amazon Bedrock AgentCore Runtime with isolated authenticated sessions.
2. THE SYSTEM SHALL keep agent business logic and tool contracts decoupled from the runtime so that the same agents can run in a local fallback mode for development and testing.
3. WHEN a conversation continues within one session, THE SYSTEM SHALL use short-term memory to preserve relevant conversational context.
4. WHEN a user opts into retained preferences, THE SYSTEM SHALL use long-term memory for permitted analytical preferences and prior interaction summaries across sessions.
5. THE SYSTEM SHALL NOT rely on AgentCore Memory as the sole record of a race result, alias, ingest run, approval, or identity-resolution decision.
6. THE SYSTEM SHALL isolate memory by authenticated user and configured application scope and SHALL prevent one coach or parent from retrieving another user's private conversational context.
7. THE SYSTEM SHALL provide the administrator with a way to inspect permitted memory metadata and each user with a way to reset or delete their retained conversational memory.
8. THE SYSTEM SHALL exclude secrets, credentials, hidden model reasoning, unnecessary minor-related personal data, and raw source registration data from long-term memory.
9. WHEN an AgentCore operation is invoked, THE SYSTEM SHALL capture operational traces for model and tool activity, latency, and errors while exposing only concise evidence summaries—not hidden chain-of-thought—to end users.
10. IF feasibility results show that AgentCore cannot satisfy the approved security, isolation, or portability requirements, THEN implementation SHALL pause for an explicit owner decision on architecture changes or a documented staged rollout. *(Amended 2026-09-20: the monthly cost profile condition was removed.)*
11. THE SYSTEM SHALL deploy every production AgentCore Runtime with `platformVersion` set to `V2` in every AWS Region the runtime is deployed to that supports it. IF the deployment Region does not support V2, THEN the runtime SHALL use `V1` and the design SHALL document the Region gap. Agent entrypoint code SHALL be written so that only values safe to share across a restored snapshot (imports, model weights, static configuration, warmed-up client objects) are computed at startup, and every value that varies per request or can expire (randomness, current time, elapsed-time references, credentials/tokens, instance identity) SHALL be computed inside the request handler. *(Added 2026-09-22.)*

### Requirement 12 — Rich web experience

**User story:** As a coach or parent, I want a polished interface that makes complex performance data easy to explore on desktop and mobile.

#### Acceptance criteria

1. THE SYSTEM SHALL replace Streamlit as the production user interface after parity and cutover criteria are met.
2. THE SYSTEM SHALL provide responsive dashboard, athlete, school, team-scoring, Saint Sebastian, agent-chat, intake, ingest-history, and resolution-review experiences appropriate to each role.
3. THE SYSTEM SHALL render sanitized Markdown, accessible data tables, and interactive declarative charts in agent responses and standard dashboard pages.
4. WHEN an agent response streams, THE SYSTEM SHALL preserve readable partial content, announce status accessibly, and recover from interruption without corrupting the conversation.
5. WHEN a chart cannot render, THE SYSTEM SHALL provide its title, description, and equivalent tabular data.
6. THE SYSTEM SHALL meet WCAG 2.1 AA expectations for keyboard navigation, focus visibility, color contrast, labels, table semantics, chart descriptions, and reduced-motion preferences.
7. THE SYSTEM SHALL support the current stable versions of major desktop and mobile browsers selected during design.
8. THE SYSTEM SHALL distinguish source data, computed metrics, agent interpretation, warnings, and human-approved decisions visually.
9. THE SYSTEM SHALL not expose administrative controls to viewer accounts.

### Requirement 13 — Authentication, authorization, and data minimization

**User story:** As the administrator, I want invite-only access and role separation because the application contains youth athlete performance records and privileged agent tools.

#### Acceptance criteria

1. THE SYSTEM SHALL require authentication for all production application, API, intake, and agent endpoints.
2. THE SYSTEM SHALL support invite-only user onboarding for a small group of coaches and parents.
3. THE SYSTEM SHALL enforce administrator and viewer permissions on the server for every protected operation rather than relying on hidden UI elements.
4. WHEN a viewer attempts an administrative operation, THE SYSTEM SHALL deny it, record the authorization failure, and make no data change or external call.
5. THE SYSTEM SHALL ingest and expose only fields required for race analytics and SHALL discard or quarantine unnecessary registration fields such as date of birth, street address, phone, and email.
6. THE SYSTEM SHALL encrypt network traffic and AWS-managed stored data using approved platform controls.
7. THE SYSTEM SHALL provide configurable session expiration and account revocation.

### Requirement 14 — Agent skills (repository-local)

**User story:** As a developer using an agentic coding assistant, I want reusable project guidance for the key technologies and domain rules so that future implementation remains consistent and safe.

*(Amended 2026-09-20 by owner decision: originally specified as installable Kiro Powers under `powers/`; converted to repository-local skills under `.github/skills/` that activate automatically for every collaborator with no per-user import step.)*

#### Acceptance criteria

1. THE REPOSITORY SHALL include automatically discovered project-local skills (`.github/skills/*/SKILL.md`) covering Strands Agents, Amazon Bedrock AgentCore, Tavily, XC domain analytics, and the SQLite/S3 durability pattern.
2. EACH skill SHALL state its purpose, activation cues, approved patterns, anti-patterns, security constraints, and links to authoritative documentation.
3. THE Strands skills SHALL cover typed tools, hooks, execution limits, model configuration, testing, and runtime portability.
4. THE AgentCore skills SHALL cover Runtime, Memory, identity, observability, and deployment relevant to this project.
5. THE Tavily skills SHALL cover Map, Crawl, Extract, source routing, credit controls, untrusted-content handling, and secret management.
6. THE XC domain skills SHALL encode race hierarchy, source terminology, distance normalization, team scoring, Saint Sebastian rules, identity-resolution policy, and known historical data gaps.
7. THE SQLite/S3 skills SHALL explicitly prohibit writable SQLite-over-S3 mounts and SHALL document local-live-copy, locking, versioning, checksum, publication, and restore patterns.
8. WHEN a skill contains a runnable example, THE EXAMPLE SHALL be validated against the project's pinned SDK version or clearly marked as illustrative pseudocode.

### Requirement 15 — AWS deployment and runaway protection

*(Amended 2026-09-20 by owner decision: the USD 20 monthly ceiling, cost
forecasting, budget notifications, and all cost-based gates are removed. Cost is
no longer a requirement, an acceptance criterion, or a release gate anywhere in
this spec. What remains below are runaway-loop safeguards, which exist to stop a
malfunctioning agent or import looping without bound — a correctness and
reliability concern, not a budget one.)*

**User story:** As the administrator, I want the platform hosted in AWS on
request-driven services with sane operational limits so that I can run it
reproducibly and a malfunctioning component cannot loop without bound.

#### Acceptance criteria

1. THE SYSTEM SHALL deploy production web, API, storage, authentication, and agent resources to AWS through reproducible infrastructure as code.
2. THE SYSTEM SHALL favor scale-to-zero or request-driven services so that idle infrastructure requires no maintenance and no always-on components are operated without a functional reason.
3. THE SYSTEM SHALL enforce a per-request token limit and a per-request tool-iteration limit on every agent invocation so that a single request cannot loop indefinitely.
4. THE SYSTEM SHALL enforce per-import page, depth, and request limits on external fetching so that one import cannot fetch without bound.
5. WHEN a runaway limit is reached, THE SYSTEM SHALL stop the affected operation, report an explanatory status, and leave read-only dashboard access unaffected.
6. THE SYSTEM SHALL NOT make any release, cutover, or feasibility gate conditional on a cost forecast or a spending threshold.

### Requirement 16 — Secret and dependency hygiene

**User story:** As the administrator, I want credentials and dependencies managed safely so that a convenient local setup does not expose keys or create supply-chain risk.

#### Acceptance criteria

1. BEFORE any implementation commit, THE REPOSITORY SHALL ignore the current `tavily_api_key` file and other approved local secret-file patterns.
2. THE SYSTEM SHALL read Tavily and AWS credentials from environment-based development configuration and approved AWS secret/parameter services in deployed environments.
3. THE REPOSITORY SHALL NOT contain plaintext production credentials in tracked files, fixtures, logs, documentation, browser bundles, or database snapshots.
4. WHEN secret scanning detects a likely credential, THE DEVELOPMENT WORKFLOW SHALL block the commit or build and identify the affected path without printing the full secret.
5. WHEN repository-history auditing finds a committed credential, THE RUNBOOK SHALL require revocation or rotation before history cleanup is considered sufficient.
6. THE SYSTEM SHALL redact credentials, authorization headers, session tokens, and sensitive query parameters from logs and traces.
7. THE PROJECT SHALL pin or lock production dependency versions and SHALL review unusual or newly introduced packages before installation.
8. THE SYSTEM SHALL use least-privilege AWS identities scoped separately for application reads, database publication, parameter access, and agent tools.

**Current audit note:** On September 19, 2026, `tavily_api_key` was confirmed untracked and absent from Git history without reading its contents. It was also confirmed not ignored; Requirement 16.1 is therefore a mandatory first implementation action.

### Requirement 17 — Feasibility analysis and architecture gates

**User story:** As the administrator, I want risky assumptions tested with the real sources and services before committing to the final architecture.

#### Acceptance criteria

1. BEFORE production implementation of dependent components, THE PROJECT SHALL produce a versioned feasibility report with reproducible probes, observed results, dates, assumptions, and go/no-go recommendations.
2. THE feasibility analysis SHALL test RunSignup race, event, year, result-set, pagination, field, and source-ID coverage across representative 2023–2026 data, including direct verification of 2023 and 2024 Meet 2 athlete-level availability.
3. THE feasibility analysis SHALL test whether a single submitted RunSignup URL and its fragment can be normalized and used to discover all intended result sets within the approved scope.
4. THE feasibility analysis SHALL compare RunSignup REST retrieval with Tavily Map, Crawl, and Extract for completeness, correctness, latency, and credit use on the supplied RunSignup URL.
5. THE feasibility analysis SHALL test Tavily fallback extraction against at least one captured or approved non-RunSignup result source if such a source is available; otherwise it SHALL document the unverified gap. *(Satisfied 2026-09-20, Task 3.3: no genuine non-RunSignup source produced usable rows; the gap was documented and, on 2026-09-21, the owner dropped the fallback from release-1 scope rather than shipping it experimental — see Requirement 6.)*
6. THE feasibility analysis SHALL measure SQLite snapshot size, startup download latency, publication latency, lock behavior, concurrent-writer rejection, interrupted-publication recovery, and restore behavior with S3.
7. THE feasibility analysis SHALL measure Strands compatibility with the selected tools and AgentCore Runtime and Memory under the intended deployment contract.
8. THE feasibility analysis SHALL compare at least one lower-cost and one higher-capability Bedrock model for analytical correctness, resolution accuracy, and latency. *(Amended 2026-09-20: token-cost comparison removed.)*
9. *(Removed 2026-09-20 by owner decision: the cost-estimation criterion no longer applies.)*
10. IF any mandatory feasibility criterion fails, THEN THE design SHALL identify a tested alternative or request an explicit scope decision before dependent tasks proceed.
11. THE report SHALL identify API terms, rate limits, robots or access constraints, and any operational dependency that could make the import strategy unreliable.

### Requirement 18 — Observability and auditability

**User story:** As the administrator, I want to understand what the application and its agents did so that errors, costs, and data decisions can be investigated.

#### Acceptance criteria

1. THE SYSTEM SHALL assign correlation identifiers across user requests, ingest runs, agent sessions, tool calls, database publications, and errors.
2. THE SYSTEM SHALL record structured operational events for extraction strategy, validation outcomes, resolution decisions, agent tool invocations, publication, restore, authorization failures, latency, and controllable usage cost.
3. THE SYSTEM SHALL distinguish concise decision evidence and tool traces from hidden model reasoning and SHALL NOT expose or persist hidden chain-of-thought.
4. WHEN an operation fails, THE SYSTEM SHALL provide an actionable user-safe error and retain a redacted diagnostic event for the administrator.
5. THE SYSTEM SHALL provide health and version information for the web API, database schema and active snapshot, agent runtime, and external connectors.
6. THE SYSTEM SHALL use configurable log and audit retention and SHALL prevent routine diagnostic logs from storing raw secrets or unnecessary personal data.

### Requirement 19 — Migration, rollout, and Heroku retirement

**User story:** As the administrator, I want a reversible migration so that the existing application remains available until the replacement is proven.

#### Acceptance criteria

1. THE SYSTEM SHALL preserve immutable backups of the selected historical source artifacts and the pre-migration canonical CSV before transformation.
2. THE migration SHALL generate a reconciliation report linking source rows to canonical results, aliases, quarantined records, and detected conflicts.
3. BEFORE cutover, THE new platform SHALL pass historical parity, security, recovery, authentication, and smoke-test gates in a staging environment. *(Amended 2026-09-21: "cost" gate removed with the cost requirements.)*
4. DURING the agreed validation window, THE existing Heroku application SHALL remain available as a read-only fallback unless the administrator explicitly chooses otherwise.
5. WHEN production cutover succeeds, THE SYSTEM SHALL document the active AWS endpoints, restore procedure, intake runbook, user administration, and support procedure. *(Amended 2026-09-21: "cost controls" removed with the cost requirements.)*
6. ONLY AFTER the administrator accepts the AWS deployment, THE PROJECT SHALL decommission Heroku resources and remove obsolete deployment files from active use.
7. IF post-cutover validation fails, THEN THE PROJECT SHALL support rollback to the last accepted database and application release without data loss.

## 8. Non-functional requirements

### Requirement 20 — Performance and resilience

1. WHEN serving cached or database-backed dashboard content under the baseline workload, THE SYSTEM SHALL target a warm p95 API response time of 500 ms or less for standard analytical queries, excluding external agent calls.
2. WHEN loading a normal dashboard page over a representative broadband connection, THE SYSTEM SHALL target primary content display within 2 seconds after a warm service response.
3. WHEN an agent request is accepted, THE SYSTEM SHALL report active progress promptly and SHALL target first meaningful streamed content within 5 seconds when the selected model and runtime are healthy.
4. WHEN importing a result set of up to 1,000 rows under normal source conditions, THE SYSTEM SHALL complete extraction, staging, and deterministic validation within 5 minutes, excluding time awaiting human review.
5. THE SYSTEM SHALL bound query rows, execution time, agent iterations, crawl pages, retries, and payload sizes to protect availability and cost.
6. IF an external source, Tavily, Bedrock, or AgentCore is unavailable, THEN approved read-only dashboard functions SHALL remain usable from the last valid database snapshot.
7. THE SYSTEM SHALL preserve a consistent approved snapshot across process restarts and failed publication attempts.

## 9. Success measures

The first production release is successful when:

1. Historical parity checks pass or every approved difference is documented with authoritative source evidence.
2. A supplied RunSignup URL imports all approved public result sets idempotently without manual browser-page saving.
3. Known school and athlete aliases resolve correctly, confirmed-distinct athletes remain separate, and ambiguous cases reach the human review queue.
4. The replacement UI provides every accepted current dashboard capability plus natural-language analysis and inline accessible charts.
5. Strands agents operate through constrained tools on AgentCore Runtime and memory, subject to the feasibility gates.
6. A database publication can be restored from S3 and an interrupted publish cannot replace the last valid version.
7. Invite-only roles prevent viewers from writing data or invoking administrative tools.
8. Heroku can be retired without loss of historical data or analytical functionality. *(Measure 8 of the original list — the USD 20 spend measure — was removed by owner decision on 2026-09-20.)*

## 10. Requirements traceability to requested outcomes

| Requested outcome | Covered by |
|---|---|
| Migrate data backend to SQLite | Requirements 1–3, 7, 19–20 |
| Add AWS Strands Agents and Kiro Powers | Requirements 10, 14, 16–18 |
| Add advanced analytics and charting | Requirements 9–10, 12, 20 |
| Move agents to Bedrock AgentCore and use memory | Requirements 11, 15, 17–18 |
| URL intake with meet, school, and athlete dedupe | Requirements 4–8, 13, 17 |
| Hybrid RunSignup REST + Tavily crawl/extract | Requirements 5–7, 17 |
| Move from Heroku to AWS | Requirements 11, 13, 15, 17, 19–20 |
| Replace Streamlit with a richer UI | Requirement 12 |
| Audit and protect Tavily/API credentials | Requirement 16 |

## 11. Source and project references

Project context:

- [Project README](../../../README.md)
- [Current dataset columns](../../../DATASET_COLUMNS.md)
- [Duplicate-name analysis](../../../DUPLICATE_NAMES_REPORT.md)
- [2023 parsing repair](../../../2023_DATA_FIX_REPORT.md)
- [Meet 2 data-gap analysis](../../../MEET2_DATA_GAP_ANALYSIS.md)
- [Current parser](../../../parse_saved_pages.py)
- [Current dashboard](../../../dashboard.py)

Technology references supplied for this initiative:

- [Strands Agents](https://strandsagents.com/)
- [Amazon Bedrock AgentCore Runtime](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html)
- [Amazon Bedrock AgentCore Memory](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html)
- [Tavily documentation](https://docs.tavily.com/)
- [Representative RunSignup results URL](https://runsignup.com/Race/Results/154050#resultSetId-691534;perpage:100)

The feasibility report and design shall record the exact documentation/API versions and observation dates used for implementation decisions.