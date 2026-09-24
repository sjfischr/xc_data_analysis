"use client";

// Overview (Task 19.1): parity with the legacy Streamlit dashboard's
// overview mode -- season/school/grade/meet filters, headline metrics,
// Saint Sebastian leaders, fastest pace, top placements, most improved,
// and cross-country team scoring with the top-15 chart.

import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import {
  Badge,
  Card,
  ChipGroup,
  DataTable,
  EntityLink,
  ErrorBanner,
  LoadingBlock,
  Medal,
  PageHeader,
  Select,
  StatGrid,
  StatTile,
  athleteHref,
  schoolHref,
} from "@/components/ui";
import { VegaChart } from "@/components/VegaChart";
import {
  api,
  type Dimensions,
  type Overview,
  type ResultFilters,
  type SaintSebastianStandingEntry,
  type SchoolSummary,
  type TeamScoreEntry,
} from "@/lib/api";
import { topTeamScoresSpec } from "@/lib/charts";
import {
  categoryLabel,
  formatClock,
  formatPace,
  formatPaceDelta,
  genderLabel,
  ordinal,
} from "@/lib/format";

function beatShare(percentile: number | null): string {
  return percentile === null ? "—" : `beat ${Math.round(percentile)}%`;
}

export default function DashboardPage() {
  return (
    <AppShell>
      <OverviewContent />
    </AppShell>
  );
}

function OverviewContent() {
  const [dimensions, setDimensions] = useState<Dimensions | null>(null);
  const [schools, setSchools] = useState<SchoolSummary[]>([]);
  const [publicationId, setPublicationId] = useState<string | null>(null);
  const [season, setSeason] = useState<number | "">("");
  const [schoolId, setSchoolId] = useState<string | "">("");
  const [division, setDivision] = useState<string | "">("");
  const [gender, setGender] = useState<string | "">("");
  const [grades, setGrades] = useState<number[]>([]);
  const [meets, setMeets] = useState<number[]>([]);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [leaders, setLeaders] = useState<SaintSebastianStandingEntry[] | null>(null);
  const [teamScores, setTeamScores] = useState<TeamScoreEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.filters(), api.schools("", 200)])
      .then(([filters, schoolList]) => {
        setDimensions(filters.data);
        setPublicationId(filters.publication_id);
        setSchools(schoolList.data);
        const latest = filters.data.season_years.at(-1);
        if (latest !== undefined) setSeason(latest);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const filters: ResultFilters = useMemo(
    () => ({
      season_year: season === "" ? undefined : season,
      school_id: schoolId || undefined,
      division_code: division || undefined,
      gender_code: gender || undefined,
      grades,
      meet_numbers: meets,
    }),
    [season, schoolId, division, gender, grades, meets],
  );

  useEffect(() => {
    if (!dimensions) return;
    setOverview(null);
    api
      .overview(filters)
      .then((env) => setOverview(env.data))
      .catch((e) => setError(String(e)));
  }, [dimensions, filters]);

  useEffect(() => {
    if (!dimensions) return;
    const scope = {
      season_year: filters.season_year,
      division_code: filters.division_code,
      gender_code: filters.gender_code,
    };
    setTeamScores(null);
    api
      .teamScores({ ...scope, school_id: filters.school_id })
      .then((env) => setTeamScores(env.data))
      .catch((e) => setError(String(e)));
    if (filters.season_year === undefined) {
      setLeaders([]);
    } else {
      api
        .saintSebastianStandings(scope)
        .then((env) => setLeaders(env.data.filter((s) => s.standing_rank === 1)))
        .catch((e) => setError(String(e)));
    }
  }, [dimensions, filters]);

  const hasFilters = schoolId || division || gender || grades.length > 0 || meets.length > 0;
  const scoresForTable = useMemo(
    () =>
      teamScores
        ? teamScores
            .filter((s) => (schoolId ? s.school_id === schoolId : s.team_rank === 1))
            .sort(
            (a, b) =>
              b.season_year - a.season_year ||
              a.meet_number - b.meet_number ||
              a.division_code.localeCompare(b.division_code) ||
              a.gender_code.localeCompare(b.gender_code) ||
              a.team_rank - b.team_rank,
          )
        : null,
    [teamScores],
  );

  return (
    <>
      <PageHeader
        eyebrow="NVJCYO Cross Country"
        title={season === "" ? "All-seasons overview" : `${season} season overview`}
        subtitle={
          publicationId ? (
            <>
              Developmental meets · data version <code className="text-xs">{publicationId.slice(0, 8)}</code>
            </>
          ) : (
            "Developmental meets"
          )
        }
      />

      <ErrorBanner error={error} />

      {dimensions && (
        <Card>
          <div className="flex flex-wrap items-end gap-4">
            <Select
              label="Season"
              value={season}
              allLabel="All seasons"
              options={[...dimensions.season_years].reverse().map((y, i) => ({
                value: y,
                label: i === 0 ? `${y} (current)` : String(y),
              }))}
              onChange={setSeason}
            />
            <Select
              label="School"
              value={schoolId}
              allLabel="All schools"
              options={schools.map((s) => ({ value: s.school_id, label: s.display_name }))}
              onChange={setSchoolId}
            />
            <Select
              label="Division"
              value={division}
              allLabel="All divisions"
              options={dimensions.divisions.map((d) => ({ value: d, label: d }))}
              onChange={setDivision}
            />
            <Select
              label="Gender"
              value={gender}
              allLabel="Boys & girls"
              options={["F", "M"].map((g) => ({ value: g, label: genderLabel(g) }))}
              onChange={setGender}
            />
            <ChipGroup
              label="Meets"
              options={dimensions.meet_numbers.map((m) => ({ value: m, label: `Meet ${m}` }))}
              selected={meets}
              onChange={setMeets}
            />
            <ChipGroup
              label="Grades"
              options={dimensions.grades.map((g) => ({ value: g, label: ordinal(g) }))}
              selected={grades}
              onChange={setGrades}
            />
            {hasFilters && (
              <button
                type="button"
                className="text-sm font-medium text-brand hover:underline"
                onClick={() => {
                  setSchoolId("");
                  setDivision("");
                  setGender("");
                  setGrades([]);
                  setMeets([]);
                }}
              >
                Clear filters
              </button>
            )}
          </div>
        </Card>
      )}

      {overview ? (
        <StatGrid label="Headline metrics">
          <StatTile label="Athletes" value={overview.metrics.athletes.toLocaleString()} tone="brand" />
          {season === "" ? (
            <StatTile label="Seasons" value={overview.metrics.seasons} />
          ) : (
            <StatTile label="Meets" value={overview.metrics.meets} />
          )}
          <StatTile label="Schools" value={overview.metrics.schools} />
          <StatTile
            label="Results"
            value={overview.metrics.results.toLocaleString()}
            hint={`${overview.metrics.athletes_with_progress.toLocaleString()} athletes with 2+ races`}
          />
        </StatGrid>
      ) : (
        <LoadingBlock rows={1} />
      )}

      {season !== "" && leaders && leaders.length > 0 && (
        <Card
          id="ss-leaders"
          title="Saint Sebastian leaders"
          description="Lowest cumulative time among athletes who have run every completed meet."
          actions={
            <a href="/standings" className="text-sm font-medium text-brand hover:underline">
              Full standings →
            </a>
          }
        >
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {leaders.map((l) => (
              <li key={`${l.division_code}-${l.gender_code}`} className="rounded-lg bg-surface-2 p-3">
                <p className="text-xs font-medium uppercase tracking-wide text-muted">
                  {categoryLabel(l.division_code, l.gender_code)}
                </p>
                <p className="mt-1 font-semibold">
                  <EntityLink href={athleteHref(l.athlete_id)}>{l.athlete_display_name}</EntityLink>
                </p>
                <p className="text-xs text-muted">
                  {l.school_display_name} · {formatClock(l.cumulative_time_ms)} over {l.meets_run}{" "}
                  meet{l.meets_run === 1 ? "" : "s"}
                </p>
              </li>
            ))}
          </ul>
        </Card>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card
          id="fastest"
          title="Fastest pace"
          description="Best pace per mile — a fair comparison across division distances."
        >
          {overview ? (
            <DataTable
              caption="Fastest pace per mile"
              dense
              rows={overview.fastest_pace}
              rowKey={(r) => r.result_id}
              empty="No pace data for these filters."
              columns={[
                { key: "rank", header: "#", cell: (_, i) => <Medal rank={i + 1} /> },
                {
                  key: "athlete",
                  header: "Athlete",
                  cell: (r) => (
                    <div className="min-w-0">
                      <EntityLink href={athleteHref(r.athlete_id)}>{r.athlete_display_name}</EntityLink>
                      <div className="text-xs text-muted">{r.school_display_name}</div>
                    </div>
                  ),
                },
                { key: "pace", header: "Pace", align: "right", cell: (r) => <strong>{formatPace(r.pace_seconds_per_mile)}</strong> },
                {
                  key: "race",
                  header: "Race",
                  cell: (r) => (
                    <span className="text-xs text-muted">
                      {r.division_code} · {r.season_year} M{r.meet_number}
                    </span>
                  ),
                },
              ]}
            />
          ) : (
            <LoadingBlock />
          )}
        </Card>

        <Card id="placements" title="Top placements" description="Top-10 overall finishes.">
          {overview ? (
            <DataTable
              caption="Top placements"
              dense
              rows={overview.top_placements}
              rowKey={(r) => r.result_id}
              empty="No placements for these filters."
              columns={[
                { key: "place", header: "Place", cell: (r) => <Medal rank={r.place_overall ?? 99} /> },
                {
                  key: "athlete",
                  header: "Athlete",
                  cell: (r) => (
                    <div>
                      <EntityLink href={athleteHref(r.athlete_id)}>{r.athlete_display_name}</EntityLink>
                      <div className="text-xs text-muted">{r.school_display_name}</div>
                    </div>
                  ),
                },
                { key: "time", header: "Time", align: "right", cell: (r) => formatClock(r.finish_time_ms) },
                {
                  key: "race",
                  header: "Race",
                  cell: (r) => (
                    <span className="text-xs text-muted">
                      {categoryLabel(r.division_code, r.gender_code)} · {r.season_year} M{r.meet_number}
                    </span>
                  ),
                },
              ]}
            />
          ) : (
            <LoadingBlock />
          )}
        </Card>
      </div>

      <Card
        id="improved"
        title="Most improved"
        description="Biggest gains in standing from first to latest race in this selection: the share of the field each runner beat. Course and weather swing whole fields' times, so standing is the fair measure."
      >
        {overview ? (
          <DataTable
            caption="Most improved athletes by standing in the field"
            dense
            rows={overview.most_improved}
            rowKey={(r) => r.athlete_id}
            empty="No athletes with two or more races in this selection."
            columns={[
              { key: "rank", header: "#", cell: (_, i) => <Medal rank={i + 1} /> },
              {
                key: "athlete",
                header: "Athlete",
                cell: (r) => (
                  <div>
                    <EntityLink href={athleteHref(r.athlete_id)}>{r.athlete_display_name}</EntityLink>
                    <div className="text-xs text-muted">
                      {r.school_display_name} · {r.division_code}
                    </div>
                  </div>
                ),
              },
              {
                key: "first",
                header: "First",
                align: "right",
                cell: (r) => (
                  <span title={`Pace ${formatPace(r.first_pace_seconds_per_mile)}`}>
                    {beatShare(r.first_percentile)}
                  </span>
                ),
              },
              {
                key: "latest",
                header: "Latest",
                align: "right",
                cell: (r) => (
                  <span title={`Pace ${formatPace(r.latest_pace_seconds_per_mile)}`}>
                    {beatShare(r.latest_percentile)}
                  </span>
                ),
              },
              {
                key: "gain",
                header: "Gain",
                align: "right",
                cell: (r) => (
                  <Badge tone={(r.improvement_percentile_points ?? 0) > 0 ? "brand" : "accent"}>
                    {(r.improvement_percentile_points ?? 0) > 0 ? "+" : ""}
                    {(r.improvement_percentile_points ?? 0).toFixed(0)} pts
                  </Badge>
                ),
              },
              { key: "races", header: "Races", align: "right", cell: (r) => r.races },
            ]}
          />
        ) : (
          <LoadingBlock />
        )}
      </Card>

      <Card
        id="team-top"
        title="Top team performances"
        description="Cross-country scoring: the sum of a team's top five finishers' places. Lower wins; teams need five finishers to score."
      >
        {teamScores === null ? (
          <LoadingBlock />
        ) : teamScores.length === 0 ? (
          <p className="text-sm text-muted">No team had five finishers in this selection.</p>
        ) : (
          <VegaChart
            spec={topTeamScoresSpec(teamScores)}
            height={Math.min(15, teamScores.length) * 24 + 40}
            label="Bar chart of the 15 lowest team scores in the selection"
          />
        )}
      </Card>

      <Card
        id="team-scores"
        title={schoolId ? "Team results by race" : "Race winners"}
        description={
          schoolId
            ? "Every race where the selected school scored."
            : "The winning team in each race. Full team scores are on the Standings page."
        }
        actions={
          <a href="/standings#team" className="text-sm font-medium text-brand hover:underline">
            All team scores →
          </a>
        }
      >
        {scoresForTable ? (
          <DataTable
            caption="Team scores by race"
            dense
            rows={scoresForTable}
            rowKey={(r) => `${r.season_year}-${r.meet_number}-${r.division_code}-${r.gender_code}-${r.school_id}`}
            empty="No qualifying teams."
            columns={[
              { key: "season", header: "Season", cell: (r) => r.season_year },
              { key: "meet", header: "Meet", cell: (r) => r.meet_number },
              { key: "race", header: "Race", cell: (r) => categoryLabel(r.division_code, r.gender_code) },
              { key: "rank", header: "Place", cell: (r) => <Medal rank={r.team_rank} /> },
              { key: "school", header: "School", cell: (r) => <EntityLink href={schoolHref(r.school_id)}>{r.school_display_name}</EntityLink> },
              { key: "score", header: "Score", align: "right", cell: (r) => <strong>{r.score}</strong> },
              { key: "avg", header: "Avg time", align: "right", cell: (r) => formatClock(r.avg_time_s === null ? null : r.avg_time_s * 1000) },
            ]}
          />
        ) : (
          <LoadingBlock />
        )}
      </Card>

      <details className="rounded-xl border border-line bg-surface p-4 text-sm shadow-card">
        <summary className="cursor-pointer font-semibold">Why pace per mile?</summary>
        <div className="mt-3 space-y-2 text-muted">
          <p>Race distances differ by division: 2nd Grade and Frosh run 2 km (1.24 mi), JV 3 km (1.86 mi), Varsity 4 km (2.49 mi).</p>
          <p>
            Pace per mile puts every race on the same scale, so athletes can be compared across divisions and
            tracked fairly as they move up. Example: an 11:37 Frosh 2 km is 5:48/mi, while a 16:50 JV 3 km is
            5:36/mi — actually faster.
          </p>
        </div>
      </details>
    </>
  );
}
