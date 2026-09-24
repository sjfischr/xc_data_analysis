"use client";

// Schools (Task 19.1). `/schools` lists every school; `/schools?id=...` is
// the school profile: season participation, team-finish history, fastest
// athletes, team scores, and the roster. Query-param routing for the same
// static-export reason as /athletes.

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppShell, useCurrentSession } from "@/components/AppShell";
import {
  Card,
  DataTable,
  EmptyState,
  EntityLink,
  ErrorBanner,
  LoadingBlock,
  Medal,
  PageHeader,
  StatGrid,
  StatTile,
  Tabs,
  athleteHref,
  schoolHref,
} from "@/components/ui";
import { VegaChart } from "@/components/VegaChart";
import {
  api,
  ApiError,
  type SchoolProfile,
  type SchoolRosterEntry,
  type SchoolSummary,
} from "@/lib/api";
import { schoolScoreHistorySpec, seasonBarsSpec } from "@/lib/charts";
import { categoryLabel, formatClock, formatPace, genderLabel, ordinal } from "@/lib/format";

export default function SchoolsPage() {
  return (
    <AppShell>
      <Suspense fallback={<LoadingBlock />}>
        <SchoolsRouter />
      </Suspense>
    </AppShell>
  );
}

function SchoolsRouter() {
  const id = useSearchParams().get("id");
  return id ? <SchoolProfileView id={id} /> : <SchoolDirectory />;
}

function SchoolDirectory() {
  const [schools, setSchools] = useState<SchoolSummary[] | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);


  useEffect(() => {
    api
      .schools("", 200)
      .then((env) => setSchools(env.data))
      .catch((e) => setError(String(e)));
  }, []);

  const visible = (schools ?? []).filter((s) =>
    s.display_name.toLowerCase().includes(query.trim().toLowerCase()),
  );

  return (
    <>
      <PageHeader eyebrow="Directory" title="Schools" subtitle={schools ? `${schools.length} schools` : undefined} />
      <ErrorBanner error={error} />
      <Card>
        <label className="flex flex-col gap-1 text-xs font-medium text-muted">
          Filter
          <input
            type="search"
            className="rounded-lg border border-line bg-surface px-3 py-2 text-base text-fg"
            placeholder="School name"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div className="mt-4">
          {schools === null ? (
            <LoadingBlock />
          ) : visible.length === 0 ? (
            <EmptyState>No schools match.</EmptyState>
          ) : (
            <ul className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {visible.map((s) => (
                <li key={s.school_id}>
                  <a
                    href={schoolHref(s.school_id)}
                    className="flex items-center gap-3 rounded-lg border border-line px-3 py-3 text-sm font-medium hover:border-brand hover:text-brand"
                  >
                    <span
                      aria-hidden
                      className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-brand-soft text-xs font-bold text-brand"
                    >
                      {initials(s.display_name)}
                    </span>
                    {s.display_name}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>
    </>
  );
}

function initials(name: string): string {
  const words = name.replace(/\bSt\.?\b/g, "St").split(/\s+/).filter(Boolean);
  return words
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() ?? "")
    .join("");
}

function rosterByGrade(roster: SchoolRosterEntry[]): [number, SchoolRosterEntry[]][] {
  const groups = new Map<number, SchoolRosterEntry[]>();
  for (const r of roster) groups.set(r.grade ?? 0, [...(groups.get(r.grade ?? 0) ?? []), r]);
  return [...groups.entries()].sort(([a], [b]) => (a || 99) - (b || 99));
}

function SchoolProfileView({ id }: { id: string }) {
  const currentSession = useCurrentSession();
  const [season, setSeason] = useState<string>("");
  const [profile, setProfile] = useState<SchoolProfile | null>(null);
  const [allSeasons, setAllSeasons] = useState<SchoolProfile["seasons"]>([]);
  const [roster, setRoster] = useState<SchoolRosterEntry[] | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const seasonYear = season ? Number(season) : undefined;
    setProfile(null);
    api
      .schoolProfile(id, seasonYear)
      .then((env) => {
        setProfile(env.data);
        setAllSeasons(env.data.seasons);
        if (!season && env.data.seasons[0]) setSeason(String(env.data.seasons[0].season_year));
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setNotFound(true);
        else setError(String(e));
      });
    api
      .schoolRoster(id, seasonYear)
      .then((env) => setRoster(env.data))
      .catch(() => setRoster([]));
  }, [id, season, reloadKey]);

  const scores = useMemo(
    () =>
      profile
        ? [...profile.team_scores].sort(
            (a, b) =>
              b.season_year - a.season_year ||
              a.meet_number - b.meet_number ||
              a.division_code.localeCompare(b.division_code) ||
              a.gender_code.localeCompare(b.gender_code),
          )
        : [],
    [profile],
  );
  const historySpec = useMemo(() => (profile ? schoolScoreHistorySpec(profile.team_scores) : null), [profile]);
  const seasonSpec = useMemo(() => seasonBarsSpec([...allSeasons].reverse()), [allSeasons]);

  if (notFound) {
    return (
      <>
        <PageHeader eyebrow="School" title="Not found" />
        <EmptyState>
          No school with that ID. <a className="text-brand underline" href="/schools">All schools</a>.
        </EmptyState>
      </>
    );
  }
  if (error) return <ErrorBanner error={error} />;

  const current = allSeasons.find((s) => String(s.season_year) === season);
  const wins = scores.filter((s) => s.team_rank === 1).length;
  const bestFinish = scores.length ? Math.min(...scores.map((s) => s.team_rank)) : null;

  return (
    <>
      <PageHeader
        eyebrow="School"
        title={profile?.display_name ?? "…"}
        subtitle={allSeasons.length ? `Seasons ${allSeasons.map((s) => s.season_year).reverse().join(", ")}` : undefined}
        actions={
          <>
            {allSeasons.length > 1 && (
              <Tabs
                label="Season"
                active={season}
                onChange={setSeason}
                tabs={allSeasons.map((s) => ({ value: String(s.season_year), label: String(s.season_year) }))}
              />
            )}
            {currentSession.role === "admin" && profile && (
              <button
                type="button"
                onClick={() => setRenaming((r) => !r)}
                className="rounded-lg border border-line px-3 py-2 text-sm font-medium hover:bg-surface-2"
              >
                Rename
              </button>
            )}
            {currentSession.agent_access && profile && (
              <a
                href={`/ask?q=${encodeURIComponent(
                  `How did ${profile.display_name} do in ${season}? Team results, strongest divisions, and standout athletes.`,
                )}`}
                className="rounded-lg bg-brand px-3 py-2 text-sm font-semibold text-on-brand hover:bg-brand-strong"
              >
                Ask about this school
              </a>
            )}
          </>
        }
      />

      {renaming && profile && (
        <RenameSchoolForm
          schoolId={id}
          currentName={profile.display_name}
          onDone={() => {
            setRenaming(false);
            setReloadKey((k) => k + 1);
          }}
        />
      )}

      {!profile ? (
        <LoadingBlock rows={6} label="Loading school profile" />
      ) : (
        <>
          <StatGrid label={`${season} summary`}>
            <StatTile label="Athletes" value={current?.athletes ?? 0} tone="brand" hint={`${season} season`} />
            <StatTile label="Results" value={current?.results ?? 0} />
            <StatTile label="Scored team races" value={scores.length} hint={wins ? `${wins} team win${wins === 1 ? "" : "s"}` : undefined} />
            <StatTile label="Best team finish" value={bestFinish ? ordinal(bestFinish) : "—"} tone="gold" />
          </StatGrid>

          <div className="grid gap-6 lg:grid-cols-3">
            <Card title="Athletes by season" className="lg:col-span-1">
              <VegaChart spec={seasonSpec} height={200} label={`Athletes per season for ${profile.display_name}`} />
            </Card>
            <Card
              title="Team finishes"
              description="Place among scoring teams in each race (1 = won)."
              className="lg:col-span-2"
            >
              {scores.length === 0 || !historySpec ? (
                <EmptyState>No race this season had five {profile.display_name} finishers.</EmptyState>
              ) : (
                <VegaChart spec={historySpec} height={200} label={`Team finish place by race for ${profile.display_name}`} />
              )}
            </Card>
          </div>

          <div className="grid gap-6 lg:grid-cols-2">
            <Card title="Fastest athletes" description="Each athlete's best pace per mile this season.">
              <DataTable
                caption="Fastest athletes by best pace"
                dense
                rows={profile.top_athletes}
                rowKey={(r) => r.athlete_id}
                empty="No timed results."
                columns={[
                  { key: "rank", header: "#", cell: (_, i) => <Medal rank={i + 1} /> },
                  { key: "athlete", header: "Athlete", cell: (r) => <EntityLink href={athleteHref(r.athlete_id)}>{r.athlete_display_name}</EntityLink> },
                  { key: "division", header: "Race", cell: (r) => <span className="text-xs text-muted">{categoryLabel(r.division_code, r.gender_code)} · M{r.meet_number}</span> },
                  { key: "pace", header: "Pace", align: "right", cell: (r) => <strong>{formatPace(r.pace_seconds_per_mile)}</strong> },
                ]}
              />
            </Card>
            <Card title="Team scores">
              <DataTable
                caption="Team scores"
                dense
                rows={scores}
                rowKey={(r) => `${r.season_year}-${r.meet_number}-${r.division_code}-${r.gender_code}`}
                empty="No scored races."
                columns={[
                  { key: "meet", header: "Meet", cell: (r) => `M${r.meet_number}` },
                  { key: "race", header: "Race", cell: (r) => categoryLabel(r.division_code, r.gender_code) },
                  { key: "rank", header: "Place", align: "center", cell: (r) => <Medal rank={r.team_rank} /> },
                  { key: "score", header: "Score", align: "right", cell: (r) => <strong>{r.score}</strong> },
                  { key: "avg", header: "Avg time", align: "right", cell: (r) => formatClock(r.avg_time_s === null ? null : r.avg_time_s * 1000) },
                ]}
              />
            </Card>
          </div>

          <Card title="Roster" description={`${roster?.length ?? 0} athletes in ${season}`}>
            {roster === null ? (
              <LoadingBlock />
            ) : roster.length === 0 ? (
              <EmptyState>No athletes on the roster this season.</EmptyState>
            ) : (
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {rosterByGrade(roster).map(([grade, athletes]) => (
                  <div key={grade}>
                    <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted">
                      {grade === 0 ? "Grade unknown" : `${ordinal(grade)} grade`} · {athletes.length}
                    </h3>
                    <ul className="text-sm">
                      {athletes.map((a) => (
                        <li key={a.athlete_id} className="flex items-center justify-between gap-2 py-0.5">
                          <EntityLink href={athleteHref(a.athlete_id)}>{a.athlete_display_name}</EntityLink>
                          <span className="text-xs text-muted">{genderLabel(a.gender_code)}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </>
      )}
    </>
  );
}

function RenameSchoolForm({
  schoolId,
  currentName,
  onDone,
}: {
  schoolId: string;
  currentName: string;
  onDone: () => void;
}) {
  const [name, setName] = useState(currentName);
  const [reason, setReason] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    try {
      await api.renameSchool(schoolId, name.trim(), reason.trim());
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setSaving(false);
    }
  }

  return (
    <Card
      title="Rename school"
      description="Changes the name everywhere, for every season. Results, rosters, and past spellings stay attached. The change is published immediately and recorded with your reason."
    >
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-xs font-medium text-muted">
          New name
          <input
            required
            minLength={2}
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="rounded-lg border border-line bg-surface px-3 py-2 text-base text-fg"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs font-medium text-muted">
          Reason (kept in the change history)
          <input
            required
            minLength={3}
            placeholder="e.g. Correct parish name: McLean, VA (Diocese of Arlington)"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="rounded-lg border border-line bg-surface px-3 py-2 text-base text-fg"
          />
        </label>
        <ErrorBanner error={error} />
        <div className="flex gap-2">
          <button
            type="submit"
            disabled={saving || name.trim() === currentName || !reason.trim()}
            className="rounded-lg bg-brand px-4 py-2 font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-40"
          >
            {saving ? "Publishing…" : "Rename and publish"}
          </button>
          <button type="button" onClick={onDone} className="rounded-lg px-4 py-2 text-sm text-muted hover:bg-surface-2">
            Cancel
          </button>
        </div>
      </form>
    </Card>
  );
}
