/** Safe reading of `searchParams` (filters live in the URL). */

export type SearchParams = Record<string, string | string[] | undefined>;

export function param(params: SearchParams, key: string): string | undefined {
  const value = params[key];
  const first = Array.isArray(value) ? value[0] : value;
  const trimmed = first?.trim();
  return trimmed ? trimmed : undefined;
}

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

/** Only accepts a valid "YYYY-MM-DD" (what <input type="date"> sends). */
export function dateParam(params: SearchParams, key: string): string | undefined {
  const value = param(params, key);
  if (!value || !ISO_DATE.test(value)) return undefined;
  return Number.isNaN(new Date(`${value}T00:00:00Z`).getTime()) ? undefined : value;
}

/** Allowed value from a closed list (e.g. status), otherwise undefined. */
export function oneOf<T extends string>(params: SearchParams, key: string, allowed: readonly T[]): T | undefined {
  const value = param(params, key);
  return allowed.includes(value as T) ? (value as T) : undefined;
}
