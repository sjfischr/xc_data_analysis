"use client";

// Athletes (Task 19.1). `/athletes` is a searchable directory;
// `/athletes?id=...` is the profile -- parity with the legacy dashboard's
// individual-athlete view (team, seasons/grade, best time, best place;
// time, pace, speed, and placement charts with trendlines; race table)
// plus season-by-season school history and a pace-trend summary.
//
// Query-param routing, not a `[id]` segment: this is a static export, and
// athlete IDs come from a live database, so they cannot be enumerated at
// build time. `useSearchParams()` needs the Suspense boundary below.

import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { AppShell, useCurrentSession } from "@/components/AppShell";
import {
  Badge,
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
import { api, ApiError, type AthleteProfile, type AthleteSummary } from "@/lib/api";
import { progressionSpec, type ProgressionMetric } from "@/lib/charts";
import {
  categoryLabel,
  formatClock,
  formatDistance,
  formatPace,
  formatPaceDelta,
  formatSpeed,
  genderLabel,
  ordinal,
} from "@/lib/format";

export default function AthletesPage() {
  return (
    <AppShell>
      <Suspense fallback={<LoadingBlock />}>
        <AthletesRouter />
      </Suspense>
    </AppShell>
  );
}

function AthletesRouter() {
  const id = useSearchParams().get("id");
  return id ? <AthleteProfileView id={id} /> : <AthleteDirectory />;
}

function AthleteDirectory() {
  const [query, setQuery] = useState("");
  const [athletes, setAthletes] = useState<AthleteSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handle = setTimeout(() => {
      api
        .athletes(query.trim(), 60)
        .then((env) => setAthletes(env.data))
        .catch((e) => setError(String(e)));
    }, 180);
    return () => clearTimeout(handle);
  }, [query]);

  return (
    <>
      <PageHeader eyebrow="Directory" title="Athletes" subtitle="Search by first or last name." />
      <ErrorBanner error={error} />
      <Card>
        <label className="flex flex-col gap-1 text-xs font-medium text-muted">
          Name
          <input
            type="search"
            autoFocus
            className="rounded-lg border border-line bg-surface px-3 py-2 text-base text-fg"
            placeholder="e.g. Walker"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <div className="mt-4">
          {athletes === null ? (
            <LoadingBlock />
          ) : athletes.length === 0 ? (
            <EmptyState>No athletes match “{query}”.</EmptyState>
          ) : (
            <ul className="grid gap-1 sm:grid-cols-2 lg:grid-cols-3">
              {athletes.map((a) => (
                <li key={a.athlete_id}>
                  <a
                    href={athleteHref(a.athlete_id)}
                    className="block rounded-lg px-3 py-2 text-sm font-medium hover:bg-surface-2 hover:text-brand"
                  >
                    {a.display_name}
                  </a>
                </li>
              ))}
            </ul>
          )}
          {athletes && athletes.length === 60 && (
            <p className="mt-3 text-xs text-muted">Showing the first 60 matches — refine the search to narrow.</p>
          )}
        </div>
      </Card>
    </>
  );
}

const CHARTS: { metric: ProgressionMetric; title: string; caption: string }[] = [
  { metric: "pace", title: "Pace per mile", caption: "Normalized by distance — fair across divisions." },
  { metric: "time", title: "Finish time", caption: "Raw time; distance varies by division." },
  { metric: "speed", title: "Speed", caption: "Miles per hour — higher is faster." },
];

function AthleteProfileView({ id }: { id: string }) {
  const currentSession = useCurrentSession();
  const [profile, setProfile] = useState<AthleteProfile | null>(null);
  const [publicationId, setPublicationId] = useState<string | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [seasonFilter, setSeasonFilter] = useState<string>("all");

  useEffect(() => {
    api
      .athleteProfile(id)
      .then((env) => {
        setProfile(env.data);
        setPublicationId(env.publication_id);
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 404) setNotFound(true);
        else setError(String(e));
      });
  }, [id]);

  const seasonYears = useMemo(
    () => (profile ? [...new Set(profile.results.map((r) => r.season_year))] : []),
    [profile],
  );
  const results = useMemo(
    () =>
      profile
        ? profile.results.filter((r) => seasonFilter === "all" || String(r.season_year) === seasonFilter)
        : [],
    [profile, seasonFilter],
  );
  const specs = useMemo(
    () => ({
      pace: progressionSpec(results, "pace"),
      time: progressionSpec(results, "time"),
      speed: progressionSpec(results, "speed"),
      place: progressionSpec(results, "place"),
    }),
    [results],
  );

  if (notFound) {
    return (
      <>
        <PageHeader eyebrow="Athlete" title="Not found" />
        <EmptyState>
          No athlete with that ID in the current data version. <a className="text-brand underline" href="/athletes">Search athletes</a>.
        </EmptyState>
      </>
    );
  }
  if (error) return <ErrorBanner error={error} />;
  if (!profile) return <LoadingBlock rows={6} label="Loading athlete profile" />;

  const latest = profile.seasons.at(-1);
  const multiSeason = profile.summary.seasons > 1;
  const trend = profile.pace_trend;
  const firstYear = seasonYears[0];
  const lastYear = seasonYears.at(-1);

  return (
    <>
      <PageHeader
        eyebrow="Athlete"
        title={profile.display_name}
        actions={
          currentSession.agent_access ? (
            <a
              href={`/ask?q=${encodeURIComponent(
                `Give me a coach's summary of ${profile.display_name}'s results: strengths, trend, and how they compare with their division.`,
              )}`}
              className="rounded-lg bg-brand px-3 py-2 text-sm font-semibold text-on-brand hover:bg-brand-strong"
            >
              Ask about {profile.display_name.split(" ")[0]}
            </a>
          ) : undefined
        }
        subtitle={
          latest ? (
            <span className="flex flex-wrap items-center gap-2">
              <EntityLink href={schoolHref(latest.school_id)}>{latest.school_display_name}</EntityLink>
              <span>·</span>
              <span>
                {latest.season_year}
                {latest.grade ? ` · ${ordinal(latest.grade)} grade` : ""} · {genderLabel(latest.gender_code)}
              </span>
              {latest.division_codes.map((d) => (
                <Badge key={d} tone="brand">
                  {d}
                </Badge>
              ))}
            </span>
          ) : undefined
        }
      />

      <StatGrid label="Career summary">
        <StatTile
          label={multiSeason ? "Seasons" : "Grade"}
          value={
            multiSeason
              ? `${profile.summary.seasons}`
              : latest?.grade
                ? ordinal(latest.grade)
                : "—"
          }
          hint={multiSeason ? `${firstYear}–${lastYear} · ${profile.summary.races} races` : `${profile.summary.races} races`}
        />
        <StatTile
          label={multiSeason ? "Career best time" : "Best time"}
          value={formatClock(profile.summary.best_time_ms, { hundredths: true })}
          tone="brand"
        />
        <StatTile
          label="Best pace"
          value={formatPace(profile.summary.best_pace_seconds_per_mile)}
          hint={`Latest ${formatPace(profile.summary.latest_pace_seconds_per_mile)}`}
          tone="accent"
        />
        <StatTile
          label="Best place"
          value={profile.summary.best_place ? `#${profile.summary.best_place}` : "—"}
          tone="gold"
        />
      </StatGrid>

      {trend && (
        <Card>
          {trend.has_confidence ? (
            <p className="text-sm">
              <strong>Pace trend:</strong> {trend.observed_change < 0 ? "faster" : "slower"} by{" "}
              <strong>{formatPaceDelta(-trend.observed_change).replace(/^[−+]/, "")}</strong> from first to latest
              race, averaging {formatPaceDelta(-(trend.slope_per_x ?? 0)).replace(/^[−+]/, "")}{" "}
              {(trend.slope_per_x ?? 0) < 0 ? "faster" : "slower"} per race across {trend.sample_size} races
              {trend.r_squared !== null && (
                <span className="text-muted">
                  {" "}
                  (fit R² {trend.r_squared.toFixed(2)}
                  {trend.r_squared < 0.3 ? " — a noisy trend; courses and conditions vary" : ""})
                </span>
              )}
              .
            </p>
          ) : (
            <p className="text-sm text-muted">
              Two races so far: pace changed {formatPaceDelta(-trend.observed_change)}. That is an observed change,
              not yet a trend.
            </p>
          )}
        </Card>
      )}

      {seasonYears.length > 1 && (
        <Tabs
          label="Season"
          active={seasonFilter}
          onChange={setSeasonFilter}
          tabs={[
            { value: "all", label: "All seasons" },
            ...seasonYears.map((y) => ({ value: String(y), label: String(y) })),
          ]}
        />
      )}

      {results.length === 0 ? (
        <EmptyState>No results recorded.</EmptyState>
      ) : (
        <>
          <div className="grid gap-6 lg:grid-cols-3">
            {CHARTS.map((c) => (
              <Card key={c.metric} title={c.title} description={c.caption}>
                <VegaChart
                  spec={specs[c.metric]}
                  height={220}
                  label={`${c.title} by race for ${profile.display_name}, with a dashed trendline`}
                />
              </Card>
            ))}
          </div>
          <Card title="Placement" description="Overall finish place; up is better.">
            <VegaChart
              spec={specs.place}
              height={240}
              label={`Overall place by race for ${profile.display_name}`}
            />
          </Card>
        </>
      )}

      <Card id="results" title="Race results">
        <DataTable
          caption={`Race results for ${profile.display_name}`}
          rows={[...results].reverse()}
          rowKey={(r) => r.result_id}
          columns={[
            ...(multiSeason ? [{ key: "season", header: "Season", cell: (r: (typeof results)[number]) => r.season_year }] : []),
            { key: "meet", header: "Meet", cell: (r) => <span>{r.meet_name}</span> },
            {
              key: "race",
              header: "Race",
              cell: (r) => (
                <span className="text-xs text-muted">
                  {categoryLabel(r.division_code, r.gender_code)} · {formatDistance(r.distance_meters)}
                </span>
              ),
            },
            { key: "place", header: "Place", align: "center", cell: (r) => (r.place_overall ? <Medal rank={r.place_overall} /> : "—") },
            { key: "time", header: "Time", align: "right", cell: (r) => formatClock(r.finish_time_ms, { hundredths: true }) },
            { key: "pace", header: "Pace", align: "right", cell: (r) => <strong>{formatPace(r.pace_seconds_per_mile)}</strong> },
            { key: "speed", header: "Speed", align: "right", cell: (r) => formatSpeed(r.speed_mph) },
          ]}
        />
      </Card>

      <Card id="seasons" title="Season history">
        <DataTable
          caption="School and grade by season"
          dense
          rows={[...profile.seasons].reverse()}
          rowKey={(s) => `${s.season_year}-${s.school_id}`}
          columns={[
            { key: "season", header: "Season", cell: (s) => s.season_year },
            { key: "school", header: "School", cell: (s) => <EntityLink href={schoolHref(s.school_id)}>{s.school_display_name}</EntityLink> },
            { key: "grade", header: "Grade", cell: (s) => (s.grade ? ordinal(s.grade) : "—") },
            { key: "division", header: "Division", cell: (s) => s.division_codes.join(", ") || "—" },
          ]}
        />
      </Card>

      {publicationId && (
        <p className="text-xs text-muted">
          Data version <code>{publicationId.slice(0, 8)}</code> ·{" "}
          <a className="hover:underline" href={athleteHref(profile.athlete_id)}>
            permalink
          </a>
        </p>
      )}
    </>
  );
}
