"use client";

// Admin intake (Task 19.3): submit a RunSignup results URL, review what
// was found, settle identity questions, and publish. Nothing reaches the
// public data until "Publish" -- submission stops at review.
//
// `/admin` lists runs; `/admin?run=<id>` is one run's review screen.

import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppShell, useCurrentSession } from "@/components/AppShell";
import {
  Badge,
  Card,
  DataTable,
  EmptyState,
  ErrorBanner,
  LoadingBlock,
  Notice,
  PageHeader,
  StatGrid,
  StatTile,
  cx,
} from "@/components/ui";
import {
  api,
  ApiError,
  type CommitResult,
  type IngestPreview,
  type IngestRun,
  type ReviewCase,
  type SchoolSummary,
} from "@/lib/api";
import { categoryLabel, formatDistance } from "@/lib/format";

export default function AdminPage() {
  return (
    <AppShell>
      <Suspense fallback={<LoadingBlock />}>
        <AdminRouter />
      </Suspense>
    </AppShell>
  );
}

function AdminRouter() {
  const session = useCurrentSession();
  const runId = useSearchParams().get("run");
  if (session.role !== "admin") {
    return (
      <>
        <PageHeader eyebrow="Admin" title="Administrators only" />
        <EmptyState>Result intake is limited to administrators.</EmptyState>
      </>
    );
  }
  return runId ? <RunReview runId={runId} /> : <IntakeHome />;
}

const STATE_TONE: Record<string, "brand" | "accent" | "gold" | "default"> = {
  committed: "brand",
  awaiting_review: "gold",
  failed: "accent",
};

function stateLabel(state: string): string {
  return state.replace(/_/g, " ");
}

function errorText(error: unknown): string {
  return error instanceof ApiError ? error.message : String(error);
}

function IntakeHome() {
  const [runs, setRuns] = useState<IngestRun[] | null>(null);
  const [url, setUrl] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .ingestRuns()
      .then((env) => setRuns(env.data))
      .catch((e) => setError(errorText(e)));
  }, []);
  useEffect(load, [load]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const env = await api.submitIngest(url.trim());
      window.location.href = `/admin?run=${encodeURIComponent(env.data.ingest_run_id)}`;
    } catch (e) {
      setError(errorText(e));
      setSubmitting(false);
      load();
    }
  }

  return (
    <>
      <PageHeader
        eyebrow="Admin"
        title="Result intake"
        subtitle="Load a meet's results from RunSignup. Nothing is published until you review and publish it."
      />
      <Card title="Add results" description="Paste the RunSignup results page for the meet, e.g. https://runsignup.com/Race/Results/154050">
        <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row">
          <label htmlFor="intake-url" className="sr-only">
            RunSignup results URL
          </label>
          <input
            id="intake-url"
            type="url"
            required
            placeholder="https://runsignup.com/Race/Results/…"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="flex-1 rounded-lg border border-line bg-surface px-3 py-2 text-base"
          />
          <button
            type="submit"
            disabled={submitting || !url.trim()}
            className="rounded-lg bg-brand px-4 py-2 font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-50"
          >
            {submitting ? "Fetching results… (up to a minute)" : "Fetch results"}
          </button>
        </form>
        <p className="mt-3 text-xs text-muted">
          Frozen seasons (2023–2025) are refused. Every result set on the page is loaded; rows that fail validation are held
          back with a reason, and names that might match an existing athlete or school wait for your decision.
        </p>
      </Card>
      <ErrorBanner error={error} />
      <Card title="Recent intake runs">
        {runs === null ? (
          <LoadingBlock />
        ) : (
          <DataTable
            caption="Recent intake runs"
            rows={runs}
            rowKey={(r) => r.ingest_run_id}
            empty="No intake runs yet."
            columns={[
              {
                key: "when",
                header: "Started",
                cell: (r) => (r.started_at ? new Date(r.started_at).toLocaleString() : "—"),
              },
              {
                key: "url",
                header: "Source",
                cell: (r) => (
                  <a href={`/admin?run=${encodeURIComponent(r.ingest_run_id)}`} className="font-medium hover:text-brand hover:underline">
                    {r.submitted_url?.replace(/^https?:\/\//, "") ?? r.ingest_run_id}
                  </a>
                ),
              },
              { key: "state", header: "State", cell: (r) => <Badge tone={STATE_TONE[r.state] ?? "default"}>{stateLabel(r.state)}</Badge> },
              { key: "review", header: "To review", align: "right", cell: (r) => r.pending_cases ?? 0 },
              { key: "held", header: "Held back", align: "right", cell: (r) => r.quarantined_count },
              { key: "added", header: "Published", align: "right", cell: (r) => r.inserted_count },
            ]}
          />
        )}
      </Card>
    </>
  );
}

function RunReview({ runId }: { runId: string }) {
  const [preview, setPreview] = useState<IngestPreview | null>(null);
  const [cases, setCases] = useState<ReviewCase[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [working, setWorking] = useState<Set<string>>(new Set());
  const [published, setPublished] = useState<CommitResult | null>(null);
  const [allSchools, setAllSchools] = useState<SchoolSummary[]>([]);
  const [otherChoice, setOtherChoice] = useState<Record<string, string>>({});
  const [publishing, setPublishing] = useState(false);

  const load = useCallback(() => {
    Promise.all([api.ingestPreview(runId), api.reviewCases(runId)])
      .then(([p, c]) => {
        setPreview(p.data);
        setCases(c.data);
      })
      .catch((e) => setError(errorText(e)));
  }, [runId]);
  useEffect(load, [load]);
  useEffect(() => {
    api
      .schools("", 200)
      .then((env) => setAllSchools(env.data))
      .catch(() => setAllSchools([]));
  }, []);

  const decide = useCallback(
    async (targets: ReviewCase[], decision: "match" | "create_new", entityId?: string) => {
      setWorking((w) => new Set([...w, ...targets.map((t) => t.resolution_case_id)]));
      setError(null);
      // Keep going past a failed decision and report every failure, so one
      // bad row never silently stops a bulk action partway through.
      const failures: string[] = [];
      try {
        for (const target of targets) {
          try {
            await api.decideCase(target.resolution_case_id, decision, entityId);
          } catch (e) {
            failures.push(`${target.raw_name ?? target.resolution_case_id}: ${errorText(e)}`);
          }
        }
        // A settled school lets its rows reach athlete resolution; ask now
        // so any new athlete questions appear here, not at publish time.
        await api.resolveIngest(runId);
      } catch (e) {
        failures.push(errorText(e));
      } finally {
        if (failures.length > 0) {
          setError(
            `${failures.length} of ${targets.length} decision${targets.length === 1 ? "" : "s"} failed ` +
              `(the rest were saved; try the failed ones again): ${failures.join("; ")}`,
          );
        }
        setWorking(new Set());
        load();
      }
    },
    [load, runId],
  );

  // Sibling suggestions: the only link to every candidate is a shared last
  // name (different first name). Almost always a brother or sister, so
  // they can be created as new athletes in one reviewed click.
  const siblingOnly = useMemo(
    () =>
      (cases ?? []).filter(
        (c) =>
          c.entity_type === "athlete" &&
          c.can_decide &&
          c.candidates.length > 0 &&
          c.candidates.every((cand) => cand.evidence_codes.includes("SAME_LAST_NAME_ONLY")),
      ),
    [cases],
  );

  // Consolidation: school cases with the same suggested match (typically
  // several spellings of one school) can be settled in one click.
  const schoolGroups = useMemo(() => {
    const groups = new Map<string, ReviewCase[]>();
    for (const c of cases ?? []) {
      if (c.entity_type !== "school" || !c.candidate_entity_id) continue;
      groups.set(c.candidate_entity_id, [...(groups.get(c.candidate_entity_id) ?? []), c]);
    }
    return groups;
  }, [cases]);

  async function publish() {
    if (!window.confirm("Publish these results? They become visible to everyone immediately.")) return;
    setPublishing(true);
    setError(null);
    try {
      const env = await api.commitIngest(runId);
      setPublished(env.data);
      load();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setPublishing(false);
    }
  }

  if (!preview || !cases) {
    return error ? <ErrorBanner error={error} /> : <LoadingBlock rows={6} label="Loading intake run" />;
  }

  const run = preview.run;
  const valid = preview.counts.valid ?? 0;
  const committed = run.state === "committed";
  const canPublish = !committed && run.state === "awaiting_review" && cases.length === 0 && valid > 0;

  return (
    <>
      <PageHeader
        eyebrow={<a href="/admin" className="hover:underline">← Result intake</a>}
        title="Review intake run"
        subtitle={<span className="break-all">{run.submitted_url}</span>}
        actions={<Badge tone={STATE_TONE[run.state] ?? "default"}>{stateLabel(run.state)}</Badge>}
      />
      <ErrorBanner error={error} />
      {run.error_summary && <Notice tone="warn">{run.error_summary}</Notice>}
      {published && (
        <Notice>
          Published {published.inserted_count} result{published.inserted_count === 1 ? "" : "s"} as data version{" "}
          <code>{published.publication_id.slice(0, 8)}</code>. The site shows them within a minute.
        </Notice>
      )}

      <StatGrid label="Run summary">
        <StatTile label="Ready" value={valid} tone="brand" hint="valid rows" />
        <StatTile label="To review" value={cases.length} tone={cases.length ? "gold" : "default"} hint="identity questions" />
        <StatTile label="Held back" value={preview.counts.quarantined ?? 0} tone={preview.counts.quarantined ? "accent" : "default"} hint="failed validation" />
        <StatTile label="Published" value={committed ? run.inserted_count : preview.counts.committed ?? 0} />
      </StatGrid>

      <Card title="Races found">
        <DataTable
          caption="Races found in this intake"
          dense
          rows={preview.races}
          rowKey={(r, i) => `${i}`}
          columns={[
            { key: "season", header: "Season", cell: (r) => r.season_year ?? "—" },
            { key: "meet", header: "Meet", cell: (r) => (r.meet_number !== null ? `Meet ${r.meet_number}` : "—") },
            { key: "race", header: "Race", cell: (r) => (r.division_code && r.gender_code ? categoryLabel(r.division_code, r.gender_code) : "—") },
            { key: "dist", header: "Distance", cell: (r) => formatDistance(r.distance_meters) },
            { key: "rows", header: "Rows", align: "right", cell: (r) => r.rows },
          ]}
        />
      </Card>

      <Card
        title={`Identity review (${cases.length})`}
        description="Each name below might be someone already in the data. Choose the match, or create a new record. Rows publish once every question is answered."
      >
        {siblingOnly.length > 1 && (
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl bg-brand-soft p-3 text-sm">
            <span>
              <strong>{siblingOnly.length}</strong> athletes only share a last name with someone already in the data
              (different first name — usually a sibling).
            </span>
            <button
              type="button"
              disabled={working.size > 0}
              onClick={() => {
                if (window.confirm(`Create ${siblingOnly.length} new athletes? Review the list below first if unsure.`)) {
                  void decide(siblingOnly, "create_new");
                }
              }}
              className="rounded-lg bg-brand px-3 py-1.5 font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-50"
            >
              Create all {siblingOnly.length} as new athletes
            </button>
          </div>
        )}
        {cases.length === 0 ? (
          <EmptyState>Nothing to review.</EmptyState>
        ) : (
          <ul className="flex flex-col gap-3">
            {[...cases]
              // Schools first: settling a school lets its runners reach
              // athlete resolution, which can add athlete questions.
              .sort((a, b) => (a.entity_type === b.entity_type ? 0 : a.entity_type === "school" ? -1 : 1))
              .map((c) => {
              const busy = working.has(c.resolution_case_id);
              const group = c.entity_type === "school" && c.candidate_entity_id ? schoolGroups.get(c.candidate_entity_id) ?? [] : [];
              return (
                <li key={c.resolution_case_id} className={cx("rounded-xl border border-line p-4", busy && "opacity-60")}>
                  <div className="flex flex-wrap items-baseline gap-2">
                    <Badge tone="gold">{c.entity_type}</Badge>
                    <span className="text-base font-semibold">{c.raw_name ?? "(no name)"}</span>
                    {c.context && <span className="text-sm text-muted">· {c.context}</span>}
                    {c.reason && <span className="text-sm text-muted">· {c.reason}</span>}
                  </div>
                  {!c.can_decide ? (
                    <p className="mt-2 text-sm text-muted">
                      This case was opened before match decisions existed. Re-run the intake to review it here.
                    </p>
                  ) : (
                    <div className="mt-3 flex flex-col gap-2">
                      {c.candidates.map((cand) => (
                        <div key={cand.entity_id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-surface-2 px-3 py-2">
                          <div className="min-w-0">
                            <p className="font-medium">{cand.display_name}</p>
                            {cand.detail && <p className="text-xs text-muted">{cand.detail}</p>}
                            {cand.conflict_codes.length > 0 && (
                              <p className="text-xs text-accent">Conflicts: {cand.conflict_codes.join(", ").replace(/_/g, " ")}</p>
                            )}
                          </div>
                          <div className="flex items-center gap-2">
                            {cand.score !== null && <span className="text-xs text-muted">{Math.round(cand.score * 100)}% match</span>}
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => void decide([c], "match", cand.entity_id)}
                              className="rounded-lg bg-brand px-3 py-1.5 text-sm font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-50"
                            >
                              Same {c.entity_type}
                            </button>
                            {group.length > 1 && cand.entity_id === c.candidate_entity_id && (
                              <button
                                type="button"
                                disabled={busy}
                                onClick={() => void decide(group, "match", cand.entity_id)}
                                className="rounded-lg border border-brand px-3 py-1.5 text-sm font-semibold text-brand hover:bg-brand-soft disabled:opacity-50"
                              >
                                Apply to all {group.length} spellings
                              </button>
                            )}
                          </div>
                        </div>
                      ))}
                      {c.entity_type === "school" && allSchools.length > 0 && (
                        <div className="flex flex-wrap items-center gap-2">
                          <label className="sr-only" htmlFor={`other-${c.resolution_case_id}`}>
                            Pick a different school
                          </label>
                          <select
                            id={`other-${c.resolution_case_id}`}
                            value={otherChoice[c.resolution_case_id] ?? ""}
                            onChange={(e) =>
                              setOtherChoice((o) => ({ ...o, [c.resolution_case_id]: e.target.value }))
                            }
                            className="rounded-lg border border-line bg-surface px-3 py-1.5 text-sm"
                          >
                            <option value="">Pick a different school…</option>
                            {allSchools.map((school) => (
                              <option key={school.school_id} value={school.school_id}>
                                {school.display_name}
                              </option>
                            ))}
                          </select>
                          <button
                            type="button"
                            disabled={busy || !otherChoice[c.resolution_case_id]}
                            onClick={() => void decide([c], "match", otherChoice[c.resolution_case_id])}
                            className="rounded-lg bg-brand px-3 py-1.5 text-sm font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-40"
                          >
                            Same school
                          </button>
                        </div>
                      )}
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void decide([c], "create_new")}
                        className="self-start rounded-lg border border-line px-3 py-1.5 text-sm font-medium hover:bg-surface-2 disabled:opacity-50"
                      >
                        New {c.entity_type} — not any of these
                      </button>
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      {preview.quarantined_rows.length > 0 && (
        <Card title="Held back" description="These rows failed validation and will not be published.">
          <DataTable
            caption="Rows held back"
            dense
            rows={preview.quarantined_rows}
            rowKey={(_, i) => `q${i}`}
            columns={[
              { key: "athlete", header: "Athlete", cell: (r) => r.athlete ?? "—" },
              { key: "team", header: "Team", cell: (r) => r.team ?? "—" },
              { key: "time", header: "Time", cell: (r) => r.time ?? "—" },
              { key: "reason", header: "Reason", cell: (r) => <span className="text-xs text-muted">{r.reason ?? "—"}</span> },
            ]}
          />
        </Card>
      )}

      {preview.sample_rows.length > 0 && (
        <Card title="Sample rows" description={`First ${preview.sample_rows.length} of ${valid + (preview.counts.committed ?? 0)}`}>
          <DataTable
            caption="Sample rows"
            dense
            rows={preview.sample_rows}
            rowKey={(_, i) => `s${i}`}
            columns={[
              { key: "place", header: "Place", cell: (r) => r.place ?? "—" },
              { key: "athlete", header: "Athlete", cell: (r) => r.athlete ?? "—" },
              { key: "team", header: "Team", cell: (r) => r.team ?? "—" },
              { key: "grade", header: "Grade", cell: (r) => r.grade ?? "—" },
              { key: "time", header: "Time", align: "right", cell: (r) => r.time ?? "—" },
            ]}
          />
        </Card>
      )}

      {!committed && (
        <div className="sticky bottom-4 z-20 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-line bg-surface p-4 shadow-card">
          <p className="text-sm text-muted">
            {cases.length > 0
              ? `Answer ${cases.length} identity question${cases.length === 1 ? "" : "s"} to publish.`
              : valid > 0
                ? `${valid} result${valid === 1 ? "" : "s"} ready to publish.`
                : "Nothing to publish."}
          </p>
          <button
            type="button"
            disabled={!canPublish || publishing}
            onClick={() => void publish()}
            className="rounded-lg bg-brand px-5 py-2 font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-40"
          >
            {publishing ? "Publishing…" : "Publish results"}
          </button>
        </div>
      )}
    </>
  );
}
