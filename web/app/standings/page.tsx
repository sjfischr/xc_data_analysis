"use client";

// Standings (Task 19.1): parity with the legacy Saint Sebastian Award
// Tracker (provisional notice, "Standings" top-3 per race and "By School"
// full lists with a highlighted school) plus team scores by meet.

import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/AppShell";
import {
  Card,
  DataTable,
  EmptyState,
  EntityLink,
  ErrorBanner,
  LoadingBlock,
  Medal,
  Notice,
  PageHeader,
  Select,
  Tabs,
  athleteHref,
  schoolHref,
} from "@/components/ui";
import {
  api,
  type Dimensions,
  type SaintSebastianStandingEntry,
  type SchoolSummary,
  type TeamScoreEntry,
} from "@/lib/api";
import { categoryLabel, formatClock } from "@/lib/format";

// Legacy dashboard.py SAINT_SEBASTIAN_REQUIRED_MEETS: the award is final
// once athletes have run all three developmental meets.
const REQUIRED_MEETS = 3;

const DIVISION_ORDER = ["2nd Grade", "Frosh", "JV", "Varsity"];

function categoryKey(e: { division_code: string; gender_code: string }): string {
  return `${e.division_code}|${e.gender_code}`;
}

function sortCategories(keys: string[]): string[] {
  return keys.sort((a, b) => {
    const [da = "", ga = ""] = a.split("|");
    const [db = "", gb = ""] = b.split("|");
    return (
      (DIVISION_ORDER.indexOf(da) + 1 || 99) - (DIVISION_ORDER.indexOf(db) + 1 || 99) ||
      ga.localeCompare(gb)
    );
  });
}

export default function StandingsPage() {
  return (
    <AppShell>
      <StandingsContent />
    </AppShell>
  );
}

function StandingsContent() {
  const [dimensions, setDimensions] = useState<Dimensions | null>(null);
  const [schools, setSchools] = useState<SchoolSummary[]>([]);
  const [season, setSeason] = useState<number | "">("");
  const [tab, setTab] = useState<"standings" | "by-school">("standings");
  const [highlightSchool, setHighlightSchool] = useState<string | "">("");
  const [standings, setStandings] = useState<SaintSebastianStandingEntry[] | null>(null);
  const [teamScores, setTeamScores] = useState<TeamScoreEntry[] | null>(null);
  const [meetFilter, setMeetFilter] = useState<number | "">("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.filters(), api.schools("", 200)])
      .then(([filters, schoolList]) => {
        setDimensions(filters.data);
        setSchools(schoolList.data);
        const latest = filters.data.season_years.at(-1);
        if (latest !== undefined) setSeason(latest);
        const lastMeet = filters.data.meet_numbers.at(-1);
        if (lastMeet !== undefined) setMeetFilter(lastMeet);
      })
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    if (season === "") return;
    setStandings(null);
    setTeamScores(null);
    api
      .saintSebastianStandings({ season_year: season })
      .then((env) => setStandings(env.data))
      .catch((e) => setError(String(e)));
    api
      .teamScores({ season_year: season })
      .then((env) => setTeamScores(env.data))
      .catch((e) => setError(String(e)));
  }, [season]);

  const byCategory = useMemo(() => {
    const groups = new Map<string, SaintSebastianStandingEntry[]>();
    for (const entry of standings ?? []) {
      const key = categoryKey(entry);
      groups.set(key, [...(groups.get(key) ?? []), entry]);
    }
    return sortCategories([...groups.keys()]).map((key) => ({
      key,
      entries: (groups.get(key) ?? []).sort((a, b) => a.standing_rank - b.standing_rank),
    }));
  }, [standings]);

  const meetsCompleted = Math.max(0, ...(standings ?? []).map((s) => s.meets_run));
  const remaining = Math.max(REQUIRED_MEETS - meetsCompleted, 0);

  const scoreGroups = useMemo(() => {
    const groups = new Map<string, TeamScoreEntry[]>();
    for (const s of teamScores ?? []) {
      if (meetFilter !== "" && s.meet_number !== meetFilter) continue;
      const key = `${s.meet_number}|${categoryKey(s)}`;
      groups.set(key, [...(groups.get(key) ?? []), s]);
    }
    const rankOf = (s: TeamScoreEntry) => [
      s.meet_number,
      DIVISION_ORDER.indexOf(s.division_code) + 1 || 99,
      s.gender_code === "F" ? 0 : 1,
    ];
    return [...groups.entries()]
      .map(([key, entries]) => ({ key, entries: entries.sort((a, b) => a.team_rank - b.team_rank) }))
      .sort((a, b) => {
        const ka = rankOf(a.entries[0]!);
        const kb = rankOf(b.entries[0]!);
        return ka[0]! - kb[0]! || ka[1]! - kb[1]! || ka[2]! - kb[2]!;
      });
  }, [teamScores, meetFilter]);

  return (
    <>
      <PageHeader
        eyebrow="Awards"
        title="Standings"
        subtitle="Saint Sebastian Award tracker and cross-country team scores."
        actions={
          dimensions && (
            <Select
              label="Season"
              value={season}
              options={[...dimensions.season_years].reverse().map((y) => ({ value: y, label: String(y) }))}
              onChange={setSeason}
            />
          )
        }
      />
      <ErrorBanner error={error} />

      <Card
        id="ss"
        title="Saint Sebastian Award"
        description={`Lowest cumulative race time. Athletes must finish all ${REQUIRED_MEETS} meets.`}
        actions={
          <Tabs
            label="Standings view"
            active={tab}
            onChange={setTab}
            tabs={[
              { value: "standings", label: "Top 3" },
              { value: "by-school", label: "By school" },
            ]}
          />
        }
      >
        {standings === null ? (
          <LoadingBlock rows={5} />
        ) : standings.length === 0 ? (
          <EmptyState>Standings appear once athletes have results for each completed meet.</EmptyState>
        ) : (
          <div className="flex flex-col gap-5">
            {remaining > 0 && (
              <Notice tone="warn">
                <strong>Provisional</strong> after {meetsCompleted} meet{meetsCompleted === 1 ? "" : "s"} —{" "}
                {remaining} meet{remaining === 1 ? "" : "s"} remaining before the award is final. Everyone listed is
                still in the hunt.
              </Notice>
            )}
            {tab === "by-school" && (
              <div className="max-w-xs">
                <Select
                  label="Highlight a school"
                  value={highlightSchool}
                  allLabel="None"
                  options={schools.map((s) => ({ value: s.school_id, label: s.display_name }))}
                  onChange={setHighlightSchool}
                />
              </div>
            )}
            <div className={tab === "standings" ? "grid gap-5 lg:grid-cols-2" : "flex flex-col gap-5"}>
              {byCategory.map(({ key, entries }) => {
                const [division = "", gender = ""] = key.split("|");
                const rows = tab === "standings" ? entries.slice(0, 3) : entries;
                return (
                  <div key={key}>
                    <h3 className="mb-2 text-sm font-semibold">
                      {categoryLabel(division, gender)}{" "}
                      <span className="font-normal text-muted">· {entries.length} eligible</span>
                    </h3>
                    <DataTable
                      caption={`Saint Sebastian standings, ${categoryLabel(division, gender)}`}
                      dense
                      rows={rows}
                      rowKey={(r) => r.athlete_id}
                      highlight={(r) => !!highlightSchool && r.school_id === highlightSchool}
                      columns={[
                        { key: "rank", header: "Rank", cell: (r) => <Medal rank={r.standing_rank} /> },
                        { key: "athlete", header: "Athlete", cell: (r) => <EntityLink href={athleteHref(r.athlete_id)}>{r.athlete_display_name}</EntityLink> },
                        { key: "school", header: "School", cell: (r) => <EntityLink href={schoolHref(r.school_id)}>{r.school_display_name}</EntityLink> },
                        { key: "time", header: "Cumulative", align: "right", cell: (r) => <strong>{formatClock(r.cumulative_time_ms, { hundredths: true })}</strong> },
                        { key: "back", header: "Back", align: "right", cell: (r) => (r.time_back_ms > 0 ? `+${formatClock(r.time_back_ms, { hundredths: true })}` : "—") },
                      ]}
                    />
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </Card>

      <Card
        id="team"
        title="Team scores"
        description="Sum of each team's top five places; lower wins. Teams need five finishers to score."
        actions={
          dimensions && (
            <Select
              label="Meet"
              value={meetFilter}
              allLabel="All meets"
              options={dimensions.meet_numbers.map((m) => ({ value: m, label: `Meet ${m}` }))}
              onChange={setMeetFilter}
            />
          )
        }
      >
        {teamScores === null ? (
          <LoadingBlock rows={5} />
        ) : scoreGroups.length === 0 ? (
          <EmptyState>No qualifying teams yet.</EmptyState>
        ) : (
          <div className="grid gap-5 lg:grid-cols-2">
            {scoreGroups.map(({ key, entries }) => {
              const [meet = "", division = "", gender = ""] = key.split("|");
              return (
                <div key={key}>
                  <h3 className="mb-2 text-sm font-semibold">
                    Meet {meet} · {categoryLabel(division, gender)}
                  </h3>
                  <DataTable
                    caption={`Team scores, meet ${meet}, ${categoryLabel(division, gender)}`}
                    dense
                    rows={entries}
                    rowKey={(r) => r.school_id}
                    columns={[
                      { key: "rank", header: "Place", cell: (r) => <Medal rank={r.team_rank} /> },
                      { key: "school", header: "School", cell: (r) => <EntityLink href={schoolHref(r.school_id)}>{r.school_display_name}</EntityLink> },
                      { key: "score", header: "Score", align: "right", cell: (r) => <strong>{r.score}</strong> },
                      { key: "avg", header: "Avg time", align: "right", cell: (r) => formatClock(r.avg_time_s === null ? null : r.avg_time_s * 1000) },
                    ]}
                  />
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </>
  );
}
