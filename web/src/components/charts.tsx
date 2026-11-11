/**
 * Simple HTML/CSS charts, rendered on the server (no chart JS in the browser).
 * Rules (dataviz guide): one color per chart (magnitude), thin bars with a rounded
 * tip and flat base, selective labels, value on hover/focus and an equivalent table.
 */
import clsx from "clsx";
import Link from "next/link";

import { formatBRL } from "@/lib/format";

export type Datum = {
  key: string;
  label: string;
  value: number;
  display?: string;
  /** Short text next to the label (e.g. "6×"). */
  hint?: string;
  /** Makes the label a link (e.g. opening the assistant with the question ready). */
  href?: string;
};

/** Axis scale: rounds the top to a "clean" number (1, 2, 2.5, 5 × 10ⁿ). */
function niceMax(max: number): number {
  if (max <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(max));
  const step = [1, 2, 2.5, 5, 10].find((s) => s * power >= max) ?? 10;
  return step * power;
}

function compactBRL(value: number): string {
  if (value >= 1000) return `R$ ${(value / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 1 })} mil`;
  return `R$ ${value.toLocaleString("pt-BR", { maximumFractionDigits: 0 })}`;
}

/** Columns per period (trend). `highlight` = highlighted column (e.g. current month). */
export function ColumnChart({ data, highlight, title }: { data: Datum[]; highlight?: string; title: string }) {
  const max = niceMax(Math.max(...data.map((d) => d.value), 0));
  const ticks = [max, max / 2, 0];
  const peak = data.reduce<Datum | undefined>((best, d) => (!best || d.value > best.value ? d : best), undefined);

  return (
    <figure>
      <div className="flex gap-2" role="img" aria-label={`${title}: ${data.map((d) => `${d.label} ${formatBRL(d.value)}`).join("; ")}`}>
        {/* Y axis: 3 ticks at round values */}
        <div className="flex h-48 flex-col justify-between pb-6 text-right text-[11px] tabular-nums text-slate-400" aria-hidden>
          {ticks.map((t) => (
            <span key={t} className="-translate-y-1/2 leading-none first:translate-y-0 last:translate-y-0">{compactBRL(t)}</span>
          ))}
        </div>
        <div className="relative flex-1">
          {/* Grid lines: hairline, solid, subtle */}
          <div className="pointer-events-none absolute inset-x-0 top-0 h-42 border-t border-slate-100" aria-hidden />
          <div className="pointer-events-none absolute inset-x-0 top-21 border-t border-slate-100" aria-hidden />
          <div className="flex h-48 items-stretch">
            {data.map((d) => {
              const pct = (d.value / max) * 100;
              const strong = d.key === highlight;
              const showLabel = strong || d.key === peak?.key;
              return (
                <div key={d.key} className="group relative flex flex-1 flex-col items-center" tabIndex={0} aria-label={`${d.label}: ${d.display ?? formatBRL(d.value)}`}>
                  <div className="relative flex h-42 w-full items-end justify-center border-b border-slate-300">
                    {showLabel && d.value > 0 && (
                      <span className="absolute text-[11px] font-medium text-slate-600" style={{ bottom: `calc(${pct}% + 4px)` }}>
                        {compactBRL(d.value)}
                      </span>
                    )}
                    <div
                      className={clsx(
                        "w-full max-w-6 rounded-t transition-colors",
                        strong ? "bg-emerald-700" : "bg-emerald-300 group-hover:bg-emerald-400 group-focus:bg-emerald-400",
                      )}
                      style={{ height: `${pct}%`, minHeight: d.value > 0 ? 2 : 0 }}
                    />
                    {/* Tooltip: value first, label after */}
                    <div className="pointer-events-none absolute bottom-full z-10 mb-1 hidden whitespace-nowrap rounded-md bg-slate-900 px-2 py-1 text-xs text-white shadow group-hover:block group-focus:block">
                      <strong>{d.display ?? formatBRL(d.value)}</strong> <span className="text-slate-300">{d.label}</span>
                    </div>
                  </div>
                  <span className={clsx("mt-1.5 text-[11px]", strong ? "font-semibold text-slate-800" : "text-slate-500")}>{d.label}</span>
                </div>
              );
            })}
          </div>
        </div>
      </div>
      <DataTable title={title} data={data} />
    </figure>
  );
}

/** Sorted horizontal bars (largest first), one color. For parts of a whole. */
export function RankedBars({ data, title, total }: { data: Datum[]; title: string; total?: number }) {
  const max = Math.max(...data.map((d) => d.value), 0) || 1;

  return (
    <figure>
      <ul className="space-y-3">
        {data.map((d) => {
          const share = total ? Math.round((d.value / total) * 100) : null;
          return (
            <li key={d.key} className="group" tabIndex={0} aria-label={`${d.label}: ${d.display ?? formatBRL(d.value)}${share !== null ? `, ${share}%` : ""}`}>
              <div className="mb-1 flex items-baseline justify-between gap-3 text-sm">
                <span className="flex min-w-0 items-baseline gap-1.5">
                  {d.href ? (
                    <Link href={d.href} className="truncate text-slate-700 hover:text-emerald-800 hover:underline">
                      {d.label}
                    </Link>
                  ) : (
                    <span className="truncate text-slate-700">{d.label}</span>
                  )}
                  {d.hint && <span className="shrink-0 text-xs text-slate-500">{d.hint}</span>}
                </span>
                <span className="shrink-0 tabular-nums text-slate-900">
                  {d.display ?? formatBRL(d.value)}
                  {share !== null && <span className="ml-1.5 text-xs text-slate-500">{share}%</span>}
                </span>
              </div>
              <div className="h-2 rounded-r bg-slate-100">
                <div
                  className="h-2 rounded-r bg-emerald-600 transition-colors group-hover:bg-emerald-700 group-focus:bg-emerald-700"
                  style={{ width: `${(d.value / max) * 100}%`, minWidth: d.value > 0 ? 2 : 0 }}
                />
              </div>
            </li>
          );
        })}
      </ul>
      <DataTable title={title} data={data} />
    </figure>
  );
}

/** Equivalent table (accessibility and people who prefer numbers). */
function DataTable({ title, data }: { title: string; data: Datum[] }) {
  return (
    <details className="mt-3 text-sm">
      <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-700">Ver como tabela</summary>
      <table className="mt-2 w-full">
        <caption className="sr-only">{title}</caption>
        <tbody className="divide-y divide-slate-100">
          {data.map((d) => (
            <tr key={d.key}>
              <th scope="row" className="py-1 text-left font-normal text-slate-600">{d.label}</th>
              <td className="py-1 text-right tabular-nums text-slate-900">{d.display ?? formatBRL(d.value)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}
