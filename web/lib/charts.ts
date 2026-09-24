// Vega-Lite spec builders for the dashboard (Task 19.1). Pure functions of
// API data -> spec; rendering and theming live in components/VegaChart.tsx.
// Tooltips use preformatted strings so a chart never shows raw ms/seconds.
// Colors may be written as `var(--token)`; VegaChart resolves them.

import type { VisualizationSpec } from "vega-embed";
import type { ResultRow, TeamScoreEntry } from "./api";
import { categoryLabel, formatClock, formatPace, formatSpeed, ordinal, raceLabel } from "./format";

// Least-squares line over the point index -- the same trend the legacy
// dashboard drew (numpy.polyfit, degree 1, over race order).
export function linearTrend(values: (number | null)[]): (number | null)[] {
  const points = values
    .map((y, x) => (y === null ? null : ([x, y] as const)))
    .filter((p): p is readonly [number, number] => p !== null);
  if (points.length < 2) return values.map(() => null);
  const n = points.length;
  const meanX = points.reduce((s, [x]) => s + x, 0) / n;
  const meanY = points.reduce((s, [, y]) => s + y, 0) / n;
  const sxx = points.reduce((s, [x]) => s + (x - meanX) ** 2, 0);
  const sxy = points.reduce((s, [x, y]) => s + (x - meanX) * (y - meanY), 0);
  const slope = sxx === 0 ? 0 : sxy / sxx;
  return values.map((_, x) => meanY + slope * (x - meanX));
}

const CLOCK_LABEL =
  "floor(datum.value / 60) + ':' + (datum.value % 60 < 10 ? '0' : '') + floor(datum.value % 60)";

export type ProgressionMetric = "standing" | "time" | "pace" | "speed" | "place";

const METRIC: Record<
  ProgressionMetric,
  {
    title: string;
    color: string;
    value: (r: ResultRow) => number | null;
    text: (r: ResultRow) => string;
    labelExpr?: string;
    reverse?: boolean;
  }
> = {
  standing: {
    title: "Share of field beaten (%)",
    color: "var(--chart-5)",
    value: (r) => (r.percentile === null || r.percentile === undefined ? null : r.percentile),
    text: (r) =>
      r.percentile === null || r.percentile === undefined ? "—" : `beat ${Math.round(r.percentile)}% of the field`,
  },
  time: {
    title: "Finish time",
    color: "var(--chart-2)",
    value: (r) => (r.finish_time_ms === null ? null : r.finish_time_ms / 1000),
    text: (r) => formatClock(r.finish_time_ms),
    labelExpr: CLOCK_LABEL,
    reverse: true,
  },
  pace: {
    title: "Pace (min/mi)",
    color: "var(--chart-1)",
    value: (r) => r.pace_seconds_per_mile,
    text: (r) => formatPace(r.pace_seconds_per_mile),
    labelExpr: CLOCK_LABEL,
    reverse: true,
  },
  speed: {
    title: "Speed (mph)",
    color: "var(--chart-3)",
    value: (r) => r.speed_mph,
    text: (r) => formatSpeed(r.speed_mph),
  },
  place: {
    title: "Overall place",
    color: "var(--chart-4)",
    value: (r) => r.place_overall,
    text: (r) => ordinal(r.place_overall),
    reverse: true,
  },
};

/** One athlete's results over time. Faster/better is always drawn *up*
 * (time, pace, and place axes are reversed) so every chart reads the same
 * way: rising line = improving. */
export function progressionSpec(results: ResultRow[], metric: ProgressionMetric): VisualizationSpec {
  const m = METRIC[metric];
  const multiSeason = new Set(results.map((r) => r.season_year)).size > 1;
  const values = results.map(m.value);
  const trend = linearTrend(values);
  const color = m.color;
  const data = results.map((r, i) => ({
    order: i,
    race: raceLabel(r.season_year, r.meet_number, multiSeason),
    value: values[i],
    trend: trend[i],
    text: m.text(r),
    meet: r.meet_name,
    division: `${r.division_code} · ${r.distance_meters ? r.distance_meters / 1000 : "?"} km`,
  }));
  const x = {
    field: "race",
    type: "ordinal" as const,
    sort: { field: "order" },
    title: null,
    axis: { labelAngle: data.length > 5 ? -40 : 0, labelPadding: 4 },
  };
  const y = {
    type: "quantitative" as const,
    title: m.title,
    scale: { zero: false, reverse: m.reverse ?? false, nice: true },
    axis: m.labelExpr ? { labelExpr: m.labelExpr, tickCount: 5 } : { tickCount: 5 },
  };
  return {
    data: { values: data },
    layer: [
      {
        mark: { type: "line", strokeDash: [5, 4], strokeWidth: 1.5, opacity: 0.55, color },
        encoding: { x, y: { ...y, field: "trend" } },
      },
      {
        mark: { type: "line", point: { filled: true, size: 70, color }, color },
        encoding: {
          x,
          y: { ...y, field: "value" },
          tooltip: [
            { field: "meet", title: "Meet" },
            { field: "division", title: "Race" },
            { field: "text", title: m.title },
          ],
        },
      },
    ],
  } as VisualizationSpec;
}

export function topTeamScoresSpec(scores: TeamScoreEntry[], count = 15): VisualizationSpec {
  const best = [...scores].sort((a, b) => a.score - b.score).slice(0, count);
  const multiSeason = new Set(best.map((s) => s.season_year)).size > 1;
  const data = best.map((s, i) => ({
    order: i,
    label: `${s.school_display_name} · ${raceLabel(s.season_year, s.meet_number, multiSeason)} ${categoryLabel(s.division_code, s.gender_code)}`,
    school: s.school_display_name,
    score: s.score,
    division: s.division_code,
    race: `${s.season_year} Meet ${s.meet_number} · ${categoryLabel(s.division_code, s.gender_code)}`,
  }));
  return {
    data: { values: data },
    mark: { type: "bar", cornerRadiusEnd: 4 },
    encoding: {
      y: { field: "label", type: "nominal", sort: { field: "order" }, title: null, axis: { labelLimit: 260 } },
      x: { field: "score", type: "quantitative", title: "Team score (sum of top 5 places — lower wins)" },
      color: { field: "division", type: "nominal", title: "Division" },
      tooltip: [
        { field: "school", title: "School" },
        { field: "race", title: "Race" },
        { field: "score", title: "Score" },
      ],
    },
  } as VisualizationSpec;
}

export function schoolScoreHistorySpec(scores: TeamScoreEntry[]): VisualizationSpec {
  const ordered = [...scores].sort(
    (a, b) => a.season_year - b.season_year || a.meet_number - b.meet_number,
  );
  const multiSeason = new Set(ordered.map((s) => s.season_year)).size > 1;
  const races: string[] = [];
  for (const s of ordered) {
    const label = raceLabel(s.season_year, s.meet_number, multiSeason);
    if (!races.includes(label)) races.push(label);
  }
  const data = ordered.map((s) => ({
    race: raceLabel(s.season_year, s.meet_number, multiSeason),
    category: categoryLabel(s.division_code, s.gender_code),
    rank: s.team_rank,
    score: s.score,
  }));
  return {
    data: { values: data },
    mark: { type: "line", point: { filled: true, size: 60 } },
    encoding: {
      x: { field: "race", type: "ordinal", sort: races, title: null, axis: { labelAngle: 0 } },
      y: {
        field: "rank",
        type: "quantitative",
        title: "Team finish (1 = won)",
        scale: { reverse: true, zero: false, nice: true },
        axis: { tickMinStep: 1, format: "d" },
      },
      color: { field: "category", type: "nominal", title: "Race" },
      tooltip: [
        { field: "category", title: "Race" },
        { field: "race", title: "Meet" },
        { field: "rank", title: "Team place" },
        { field: "score", title: "Score" },
      ],
    },
  } as VisualizationSpec;
}

export function seasonBarsSpec(
  seasons: { season_year: number; athletes: number }[],
): VisualizationSpec {
  return {
    data: { values: seasons.map((s) => ({ season: String(s.season_year), athletes: s.athletes })) },
    mark: { type: "bar", cornerRadiusEnd: 4, color: "var(--chart-1)" },
    encoding: {
      x: { field: "season", type: "ordinal", title: null, axis: { labelAngle: 0 } },
      y: { field: "athletes", type: "quantitative", title: "Athletes" },
      tooltip: [
        { field: "season", title: "Season" },
        { field: "athletes", title: "Athletes" },
      ],
    },
  } as VisualizationSpec;
}
