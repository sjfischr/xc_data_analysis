# Frontend workspace

A minimal Next.js **static export** shell (Task 14.1, design.md §3.3): a
login page (`/login`), a client-side route guard (`lib/useSession.ts`), and
one real working page (`/dashboard`) that calls the FastAPI backend (Task
13) for its data. TypeScript + Tailwind CSS are wired; **shadcn/ui,
TanStack Query/Table, sanitized Markdown rendering, and Vega-Lite chart
rendering are not added yet** -- honest gap, see tasks.md Task 14.1's note.
Tasks 14.2-14.6's page inventory (full dashboard rebuild, advanced
athlete/school views, Ask-the-Data chat UI, admin intake/resolution/
operations UIs, and WCAG 2.1 AA validation) are **not built** -- also
recorded honestly in tasks.md rather than shipped as shallow stubs.

## Background conversations

Ask the Data submits `POST /api/v1/chat/jobs` with a UUID generated before
the request. An uncertain submission is retried only explicitly, using
the same UUID. Answers replay from the durable job event endpoint, draining
all pages with its cursor. Normal polling is every second; transient failures
back off to 30 seconds without resubmitting questions.
After an uncertain submission, refresh recovers the server's job ID and
question from history; pending jobs offer the same safe retry. Submission
conflicts re-list durable jobs and remove rejected local placeholders,
returning the rejected question to the composer. Server capacity errors
(50 jobs per conversation; 200 conversations per account) are shown explicitly.

Only an account-scoped conversation UUID is saved in browser storage.
Prompts, answers, charts, and cursors are never persisted there. Refresh
loads the latest (up to 50) jobs and replays their events. Leaving the page
stops watching, not the job; **Stop** explicitly requests cancellation.
**New conversation** saves a new ID. **Delete my history** deletes server
memory and jobs, clears the current UI, and removes the stored ID.
Saved jobs remain accessible when new submissions are disabled. Deletion
can be refused while any of your jobs are active (including older
conversations); the server's explanation is shown without clearing history.
An account change hides the previous conversation and requires a reload.
Focus-time account checks visually mask the conversation without unmounting
or changing the composer's layout/focus, so an in-flight first input event
survives revalidation. Unverified content is also hidden from accessibility
navigation and pointer interaction until the account check succeeds.
If browser storage is unavailable, background work continues but refresh
cannot recover that conversation ID.

Frontend regression tests use Node's built-in runner and the existing
locked TypeScript compiler, with mocked fetch and fixture-only output:

```powershell
pnpm exec node --test tests\chat.test.cjs
pnpm typecheck
pnpm build
```

## Running it locally

```bash
# Terminal 1: a real local backend (in-memory S3, a fresh local SQLite db).
# From the repository root, not this directory:
PYTHONPATH=src python -m xc_platform.cli.run_local_api

# Terminal 2:
pnpm dev
# then open http://localhost:3000 -- /login defaults to the explicitly-
# marked local-only dev-login stand-in (see api/routers/auth.py). A real
# Cognito hosted-UI flow does exist and is live in production (Task
# 18.2) -- see "Production build" below for how to point this page at it.
```

## Production build

A real deploy needs two build-time environment variables `pnpm build`
does not set by default (no CI/CD pipeline injects either automatically
yet -- the same already-accepted gap `NEXT_PUBLIC_API_BASE_URL` itself
has always had):

```bash
NEXT_PUBLIC_AUTH_MODE=cognito NEXT_PUBLIC_API_BASE_URL=https://<real-api-host> pnpm build
```

`NEXT_PUBLIC_AUTH_MODE=cognito` switches `/login` from the dev-login form
to a real link to `GET /api/v1/auth/login` (Cognito's hosted UI). Leaving
it unset (or anything other than `"cognito"`) keeps the dev-login form --
this is what `pnpm dev`/local testing always uses.

Verified this session: `pnpm typecheck` and `pnpm build` (the real static
export) both pass, and the full login -> session -> `/catalog/filters` ->
`/athletes` request flow was proven end to end against the real running
backend (matching `lib/api.ts`'s types exactly). **Not verified**: visual
rendering, keyboard navigation, or contrast in an actual browser GUI -- no
graphical browser was available in the environment this was built in, only
HTTP-level checks (`curl`, TypeScript compilation, the production build).

## Why the scaffold exists now

Requirement 16.7 requires locked production dependency versions and a review
step for newly introduced packages. That guarantee has to exist *before* the
first package is added, otherwise the first install is unreviewed and unlocked.
So Task 1.2 lands the lockfile, the pinned toolchain, and the CI gate first.

## Pinned toolchain

| Tool | Version | Pinned in |
| --- | --- | --- |
| Node | 20.19.1 | `package.json` (`engines`), CI `setup-node` |
| pnpm | 10.26.1 | `package.json` (`packageManager`, `engines`), CI `pnpm/action-setup` |

`pnpm` is the selected package manager. Do not use `npm install` or
`yarn` here; doing so would create a competing lockfile and defeat the
integrity check.

## Commands

```bash
# Reproduce the locked tree exactly. Fails if package.json and the lockfile
# disagree. This is what CI runs.
pnpm install --frozen-lockfile

# Vulnerability audit. Fails on moderate-or-higher advisories.
pnpm audit --audit-level moderate
```

## Adding a dependency

Adding a package is a reviewed change. Follow
[docs/dependency-review.md](../docs/dependency-review.md) — in short:

```bash
# save-exact=true in .npmrc means this writes an exact version, not a range.
pnpm add <package>@<exact-version>
```

Then commit the updated `package.json` **and** `pnpm-lock.yaml` together in the
same commit, and record the review notes in the pull request.

## Local hardening notes

`.npmrc` sets `ignore-scripts=true`, so transitive dependencies cannot run
lifecycle scripts on install. If a legitimate dependency genuinely needs a
build step, allowlist it explicitly during dependency review rather than
disabling the setting globally.
