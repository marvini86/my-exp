/** Dashboard periods. Dates on the Brasília calendar (the family lives there, the server runs in UTC). */

export const PERIOD_PRESETS = {
  "3m": { label: "Últimos 3 meses", months: 3 },
  "6m": { label: "Últimos 6 meses", months: 6 },
  "12m": { label: "Últimos 12 meses", months: 12 },
  ano: { label: "Este ano", months: null },
} as const;

export type PeriodPreset = keyof typeof PERIOD_PRESETS;
export const DEFAULT_PRESET: PeriodPreset = "6m";

/** "Today" in Brasília as YYYY-MM-DD. */
export function todayInBrazil(now: Date = new Date()): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "America/Sao_Paulo" }).format(now);
}

function monthIndex(isoDate: string): number {
  const [y, m] = isoDate.split("-").map(Number);
  return y! * 12 + (m! - 1);
}

function fromIndex(index: number): string {
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;
}

/** Preset period: from the 1st day of the starting month until today. */
export function presetRange(preset: PeriodPreset, today: string): { start: string; end: string } {
  const { months } = PERIOD_PRESETS[preset];
  const start = months === null ? `${today.slice(0, 4)}-01-01` : `${fromIndex(monthIndex(today) - (months - 1))}-01`;
  return { start, end: today };
}

/** Every "YYYY-MM" month between two dates (inclusive) — so the chart does not skip months without spending. */
export function monthsBetween(start: string, end: string): string[] {
  const out: string[] = [];
  for (let i = monthIndex(start); i <= monthIndex(end); i++) out.push(fromIndex(i));
  return out;
}

export function previousMonth(yearMonth: string): string {
  return fromIndex(monthIndex(`${yearMonth}-01`) - 1);
}
