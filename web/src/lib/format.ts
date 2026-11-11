/** pt-BR formatting. Monetary values arrive from the API as a decimal string ("63.43"). */

const brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const day = new Intl.DateTimeFormat("pt-BR", { timeZone: "UTC" });
// Date/time (API timestamps, in UTC) in the family's time zone.
const dateTime = new Intl.DateTimeFormat("pt-BR", {
  dateStyle: "short",
  timeStyle: "short",
  timeZone: "America/Sao_Paulo",
});
const month = new Intl.DateTimeFormat("pt-BR", { month: "short", year: "numeric", timeZone: "UTC" });

export function formatBRL(amount: string | number | null | undefined): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  const value = typeof amount === "number" ? amount : Number(amount);
  return Number.isFinite(value) ? brl.format(value) : "—";
}

/** "2026-10-05" → "05/10/2026". Dates without time stay in UTC (the day does not shift). */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso.length === 10 ? `${iso}T00:00:00Z` : iso);
  return Number.isNaN(date.getTime()) ? "—" : day.format(date);
}

/** "2026-10" → "out. de 2026". */
export function formatMonth(yearMonth: string): string {
  const date = new Date(`${yearMonth}-01T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? yearMonth : month.format(date);
}

/** "2026-11-07T01:30:00Z" → "06/11/2026, 22:30" (Brasília time). */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? "—" : dateTime.format(date);
}
