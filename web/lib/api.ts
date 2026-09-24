// Typed client for the FastAPI backend (Task 13). Every call sends cookies
// (`credentials: "include"`) so the server-side session (design.md 6.2) is
// used -- never a bearer token in a header or, worse, a URL.

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match?.[1] !== undefined ? decodeURIComponent(match[1]) : null;
}

async function request<T>(
  path: string,
  options: { method?: string; body?: unknown } = {},
): Promise<T> {
  const method = options.method ?? "GET";
  const headers: Record<string, string> = {};
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  // Double-submit CSRF token (api/deps.py) on every mutating request.
  if (method !== "GET" && method !== "HEAD") {
    const csrf = getCookie("xc_csrf");
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    credentials: "include",
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    const code = payload?.error?.code ?? "unknown_error";
    const message = payload?.error?.message ?? `request failed with status ${response.status}`;
    throw new ApiError(response.status, code, message);
  }
  return payload as T;
}

export interface Envelope<T> {
  request_id: string;
  publication_id: string | null;
  data: T;
}

export interface SessionInfo {
  actor_id: string;
  role: "admin" | "viewer";
  agent_access: boolean;
}

export interface Dimensions {
  season_years: number[];
  divisions: string[];
  gender_codes: string[];
  school_count: number;
  athlete_count: number;
  meet_numbers: number[];
  grades: number[];
}

export interface AthleteSummary {
  athlete_id: string;
  display_name: string;
}

export interface SchoolSummary {
  school_id: string;
  display_name: string;
}

export interface TeamScoreEntry {
  season_year: number;
  meet_number: number;
  division_code: string;
  gender_code: string;
  school_id: string;
  school_display_name: string;
  score: number;
  scoring_runners: number;
  avg_time_s: number | null;
  team_rank: number;
}

export interface SaintSebastianStandingEntry {
  season_year: number;
  division_code: string;
  gender_code: string;
  athlete_id: string;
  athlete_display_name: string;
  school_id: string;
  school_display_name: string;
  cumulative_time_ms: number;
  meets_run: number;
  standing_rank: number;
  time_back_ms: number;
}

export interface ProgressionEntry {
  season_year: number;
  meet_name: string;
  division_code: string;
  distance_meters: number | null;
  finish_time_ms: number | null;
  place_overall: number | null;
  pace_seconds_per_mile: number | null;
}

export interface TrendSummary {
  implementation_version: string;
  sample_size: number;
  has_confidence: boolean;
  observed_change: number;
  slope_per_x: number | null;
  r_squared: number | null;
  span: number | null;
}

export interface AthleteProgressionResponse {
  athlete_id: string;
  entries: ProgressionEntry[];
  pace_trend: TrendSummary | null;
}

export interface SchoolRosterEntry {
  athlete_id: string;
  athlete_display_name: string;
  season_year: number;
  grade: number | null;
  gender_code: string;
}

export interface ResultRow {
  result_id: string;
  race_id: string;
  meet_id: string;
  athlete_id: string;
  athlete_display_name: string;
  school_id: string;
  school_display_name: string;
  season_year: number;
  meet_number: number | null;
  meet_name: string;
  meet_date: string | null;
  division_code: string;
  gender_code: string;
  distance_meters: number | null;
  finish_time_ms: number | null;
  place_overall: number | null;
  grade: number | null;
  pace_seconds_per_mile: number | null;
  speed_mph: number | null;
}

export interface OverviewMetrics {
  athletes: number;
  schools: number;
  meets: number;
  seasons: number;
  results: number;
  athletes_with_progress: number;
}

export interface ImprovementEntry {
  athlete_id: string;
  athlete_display_name: string;
  school_display_name: string;
  division_code: string;
  races: number;
  first_pace_seconds_per_mile: number;
  latest_pace_seconds_per_mile: number;
  improvement_seconds_per_mile: number;
  improvement_pct: number;
}

export interface Overview {
  metrics: OverviewMetrics;
  fastest_pace: ResultRow[];
  top_placements: ResultRow[];
  most_improved: ImprovementEntry[];
}

export interface AthleteSeason {
  season_year: number;
  school_id: string;
  school_display_name: string;
  grade: number | null;
  gender_code: string;
  division_codes: string[];
}

export interface AthleteProfile {
  athlete_id: string;
  display_name: string;
  seasons: AthleteSeason[];
  summary: {
    races: number;
    seasons: number;
    best_time_ms: number | null;
    best_pace_seconds_per_mile: number | null;
    best_place: number | null;
    latest_pace_seconds_per_mile: number | null;
  };
  results: ResultRow[];
  pace_trend: TrendSummary | null;
}

export interface SchoolProfile {
  school_id: string;
  display_name: string;
  seasons: { season_year: number; athletes: number; results: number }[];
  team_scores: TeamScoreEntry[];
  top_athletes: ResultRow[];
}

export interface ResultFilters {
  season_year?: number;
  school_id?: string;
  athlete_id?: string;
  division_code?: string;
  gender_code?: string;
  meet_numbers?: number[];
  grades?: number[];
  race_id?: string;
}

function resultQuery(filters: ResultFilters): string {
  const qs = new URLSearchParams();
  if (filters.season_year !== undefined) qs.set("season_year", String(filters.season_year));
  if (filters.school_id) qs.set("school_id", filters.school_id);
  if (filters.athlete_id) qs.set("athlete_id", filters.athlete_id);
  if (filters.division_code) qs.set("division_code", filters.division_code);
  if (filters.gender_code) qs.set("gender_code", filters.gender_code);
  if (filters.race_id) qs.set("race_id", filters.race_id);
  for (const meet of filters.meet_numbers ?? []) qs.append("meet_number", String(meet));
  for (const grade of filters.grades ?? []) qs.append("grade", String(grade));
  return qs.toString();
}

export interface IngestRun {
  ingest_run_id: string;
  state: string;
  inserted_count: number;
  quarantined_count: number;
  error_summary: string | null;
  submitted_url?: string;
  started_at?: string;
  finished_at?: string | null;
  result_publication_id?: string | null;
  pending_cases?: number;
}

export interface IngestPreview {
  run: IngestRun;
  counts: Record<string, number>;
  races: {
    season_year: number | null;
    meet_number: number | null;
    division_code: string | null;
    gender_code: string | null;
    distance_meters: number | null;
    rows: number;
  }[];
  sample_rows: { athlete: string | null; team: string | null; place: number | null; time: string | null; grade: number | null }[];
  quarantined_rows: { athlete: string | null; team: string | null; place: number | null; time: string | null; reason: string | null }[];
  pending_cases: number;
}

export interface ReviewCandidate {
  entity_id: string;
  display_name: string;
  detail: string | null;
  score: number | null;
  evidence_codes: string[];
  conflict_codes: string[];
}

export interface ReviewCase {
  resolution_case_id: string;
  entity_type: "athlete" | "school";
  ingest_run_id: string | null;
  status: string;
  confidence: number | null;
  candidate_entity_id: string | null;
  raw_name: string | null;
  context: string | null;
  season_year: number | null;
  reason: string | null;
  can_decide: boolean;
  candidates: ReviewCandidate[];
}

export interface CommitResult {
  ingest_run_id: string;
  publication_id: string;
  inserted_count: number;
  already_committed: boolean;
}

interface StandingsFilters {
  season_year?: number;
  division_code?: string;
  gender_code?: string;
  school_id?: string;
}

function standingsQuery(filters: StandingsFilters): string {
  const qs = new URLSearchParams();
  if (filters.season_year !== undefined) qs.set("season_year", String(filters.season_year));
  if (filters.division_code) qs.set("division_code", filters.division_code);
  if (filters.gender_code) qs.set("gender_code", filters.gender_code);
  if (filters.school_id) qs.set("school_id", filters.school_id);
  return qs.toString();
}

export const api = {
  ingestRuns: () => request<Envelope<IngestRun[]>>("/api/v1/ingest-runs"),
  submitIngest: (url: string) =>
    request<Envelope<IngestRun>>("/api/v1/ingest-runs", { method: "POST", body: { url } }),
  ingestPreview: (runId: string) =>
    request<Envelope<IngestPreview>>(`/api/v1/ingest-runs/${encodeURIComponent(runId)}/preview`),
  reviewCases: (runId?: string) =>
    request<Envelope<ReviewCase[]>>(
      `/api/v1/resolution-cases${runId ? `?ingest_run_id=${encodeURIComponent(runId)}` : ""}`,
    ),
  decideCase: (
    caseId: string,
    decision: "match" | "create_new" | "reject",
    entityId?: string,
  ) => {
    const qs = new URLSearchParams({ decision_type: decision });
    if (entityId) qs.set("entity_id", entityId);
    return request<Envelope<{ resolution_decision_id: string; entity_id?: string }>>(
      `/api/v1/resolution-cases/${encodeURIComponent(caseId)}/decisions?${qs.toString()}`,
      { method: "POST" },
    );
  },
  resolveIngest: (runId: string) =>
    request<Envelope<IngestRun>>(`/api/v1/ingest-runs/${encodeURIComponent(runId)}/resolve`, {
      method: "POST",
    }),
  renameSchool: (schoolId: string, name: string, reason: string) =>
    request<Envelope<{ school_id: string; display_name: string }>>(
      `/api/v1/schools/${encodeURIComponent(schoolId)}/rename`,
      { method: "POST", body: { name, reason } },
    ),
  commitIngest: (runId: string) =>
    request<Envelope<CommitResult>>(`/api/v1/ingest-runs/${encodeURIComponent(runId)}/commit`, {
      method: "POST",
    }),
  session: () => request<SessionInfo>("/api/v1/auth/session"),
  logout: () => request<{ status: string }>("/api/v1/auth/logout", { method: "POST" }),
  filters: () => request<Envelope<Dimensions>>("/api/v1/catalog/filters"),
  athletes: (q: string, limit = 20) =>
    request<Envelope<AthleteSummary[]>>(
      `/api/v1/athletes?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
  schools: (q: string, limit = 20) =>
    request<Envelope<SchoolSummary[]>>(
      `/api/v1/schools?q=${encodeURIComponent(q)}&limit=${limit}`,
    ),
  teamScores: (filters: StandingsFilters) =>
    request<Envelope<TeamScoreEntry[]>>(`/api/v1/team-scores?${standingsQuery(filters)}`),
  saintSebastianStandings: (filters: StandingsFilters) =>
    request<Envelope<SaintSebastianStandingEntry[]>>(
      `/api/v1/saint-sebastian-standings?${standingsQuery(filters)}`,
    ),
  results: (filters: ResultFilters) =>
    request<Envelope<ResultRow[]>>(`/api/v1/results?${resultQuery(filters)}`),
  overview: (filters: ResultFilters) =>
    request<Envelope<Overview>>(`/api/v1/overview?${resultQuery(filters)}`),
  athleteProfile: (athleteId: string) =>
    request<Envelope<AthleteProfile>>(
      `/api/v1/athletes/${encodeURIComponent(athleteId)}/profile`,
    ),
  schoolProfile: (schoolId: string, seasonYear?: number) => {
    const qs = new URLSearchParams();
    if (seasonYear !== undefined) qs.set("season_year", String(seasonYear));
    return request<Envelope<SchoolProfile>>(
      `/api/v1/schools/${encodeURIComponent(schoolId)}/profile?${qs.toString()}`,
    );
  },
  athleteProgression: (athleteId: string) =>
    request<Envelope<AthleteProgressionResponse>>(
      `/api/v1/athletes/${encodeURIComponent(athleteId)}/progression`,
    ),
  schoolRoster: (schoolId: string, seasonYear?: number) => {
    const qs = new URLSearchParams();
    if (seasonYear !== undefined) qs.set("season_year", String(seasonYear));
    return request<Envelope<SchoolRosterEntry[]>>(
      `/api/v1/schools/${encodeURIComponent(schoolId)}/roster?${qs.toString()}`,
    );
  },
};
