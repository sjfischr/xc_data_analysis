// Display formatting shared by every page and chart (Task 19.1). Times are
// stored as integer milliseconds and paces as seconds per mile; these are
// the only places they turn into strings.

export function formatClock(ms: number | null | undefined, { hundredths = false } = {}): string {
  if (ms === null || ms === undefined) return "—";
  const totalSeconds = ms / 1000;
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const secondsText = hundredths
    ? seconds.toFixed(2).padStart(5, "0")
    : String(Math.floor(seconds)).padStart(2, "0");
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${secondsText}`
    : `${minutes}:${secondsText}`;
}

export function formatPace(secondsPerMile: number | null | undefined): string {
  if (secondsPerMile === null || secondsPerMile === undefined) return "—";
  const rounded = Math.round(secondsPerMile);
  return `${Math.floor(rounded / 60)}:${String(rounded % 60).padStart(2, "0")}/mi`;
}

export function formatPaceDelta(seconds: number): string {
  const sign = seconds > 0 ? "−" : seconds < 0 ? "+" : "";
  const abs = Math.abs(Math.round(seconds));
  return abs >= 60
    ? `${sign}${Math.floor(abs / 60)}:${String(abs % 60).padStart(2, "0")}/mi`
    : `${sign}${abs}s/mi`;
}

export function formatSpeed(mph: number | null | undefined): string {
  return mph === null || mph === undefined ? "—" : `${mph.toFixed(2)} mph`;
}

export function genderLabel(code: string): string {
  return code === "F" ? "Girls" : code === "M" ? "Boys" : code === "X" ? "Open" : code;
}

export function categoryLabel(divisionCode: string, genderCode: string): string {
  return `${divisionCode} ${genderLabel(genderCode)}`;
}

export function ordinal(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  const mod100 = n % 100;
  const suffix =
    mod100 >= 11 && mod100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `${n}${suffix}`;
}

export function raceLabel(seasonYear: number, meetNumber: number | null, multiSeason: boolean): string {
  const meet = meetNumber === null ? "Champ" : `M${meetNumber}`;
  return multiSeason ? `${seasonYear} ${meet}` : meet;
}

export function formatDistance(meters: number | null | undefined): string {
  if (!meters) return "—";
  return `${(meters / 1000).toFixed(meters % 1000 === 0 ? 0 : 1)} km`;
}
