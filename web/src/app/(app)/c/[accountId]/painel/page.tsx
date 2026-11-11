import clsx from "clsx";
import type { Metadata } from "next";
import { AlertCircle, ArrowDownRight, ArrowUpRight, CalendarClock, FileUp, LayoutDashboard } from "lucide-react";
import Link from "next/link";

import { ColumnChart, RankedBars, type Datum } from "@/components/charts";
import { navItem } from "@/components/shell/nav";
import { Alert, Card, EmptyState, PageHeader, buttonClass } from "@/components/ui";
import { api, unwrap } from "@/lib/api/server";
import { formatBRL, formatDate, formatMonth } from "@/lib/format";
import {
  DEFAULT_PRESET,
  PERIOD_PRESETS,
  monthsBetween,
  presetRange,
  previousMonth,
  todayInBrazil,
  type PeriodPreset,
} from "@/lib/period";
import { oneOf, type SearchParams } from "@/lib/query";
import { getAccount } from "@/lib/session";

export const metadata: Metadata = { title: "Painel" };

const PRESETS = Object.keys(PERIOD_PRESETS) as PeriodPreset[];

function toNumber(value: string): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : 0;
}

function daysUntil(isoDate: string, today: string): number {
  return Math.round((Date.parse(`${isoDate}T00:00:00Z`) - Date.parse(`${today}T00:00:00Z`)) / 86_400_000);
}

export default async function DashboardPage({
  params,
  searchParams,
}: {
  params: Promise<{ accountId: string }>;
  searchParams: Promise<SearchParams>;
}) {
  const { accountId } = await params;
  const query = await searchParams;
  const account = await getAccount(accountId);

  const preset = oneOf(query, "periodo", PRESETS) ?? DEFAULT_PRESET;
  const today = todayInBrazil();
  const range = presetRange(preset, today);

  const client = await api();
  const [summary, recent] = await Promise.all([
    unwrap(
      client.GET("/accounts/{account_id}/reports/summary", {
        params: { path: { account_id: account.id }, query: range },
      }),
    ),
    unwrap(
      client.GET("/accounts/{account_id}/entries/", {
        params: { path: { account_id: account.id }, query: { limit: 8 } },
      }),
    ),
  ]);

  const basePath = `/c/${account.id}`;
  const item = navItem("painel");

  // Months without spending count as zero (the chart does not "skip" months). Totals come from the API.
  const byMonth = new Map(summary.by_month.map((m) => [m.month, m]));
  const months = monthsBetween(summary.period.start, summary.period.end);
  const monthData: Datum[] = months.map((month) => ({
    key: month,
    label: formatMonth(month).replace(" de ", "/").replace(".", ""),
    value: toNumber(byMonth.get(month)?.total ?? "0"),
  }));

  const currentMonth = today.slice(0, 7);
  const current = byMonth.get(currentMonth);
  const previous = byMonth.get(previousMonth(currentMonth));
  const currentTotal = toNumber(current?.total ?? "0");
  const previousTotal = toNumber(previous?.total ?? "0");
  const delta = previousTotal > 0 ? Math.round(((currentTotal - previousTotal) / previousTotal) * 100) : null;

  const groups = (rows: typeof summary.by_category): Datum[] =>
    rows.map((g, i) => ({ key: g.key ?? `none-${i}`, label: g.label, value: toNumber(g.total) }));
  const total = toNumber(summary.total);
  const products: Datum[] = summary.top_products.map((p) => ({
    key: p.key ?? p.label,
    label: p.label,
    value: toNumber(p.total),
    hint: p.count > 1 ? `${p.count}×` : undefined,
    href: `${basePath}/assistente?pergunta=${encodeURIComponent(`Quanto gastei com ${p.label}?`)}`,
  }));

  const periodFilter = (
    <div className="mb-6 flex flex-wrap gap-2" aria-label="Período">
      {PRESETS.map((p) => (
        <Link
          key={p}
          href={p === DEFAULT_PRESET ? `${basePath}/painel` : `${basePath}/painel?periodo=${p}`}
          aria-current={p === preset ? "true" : undefined}
          className={clsx(
            "rounded-full px-3 py-1 text-sm font-medium transition",
            p === preset ? "bg-emerald-700 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50",
          )}
        >
          {PERIOD_PRESETS[p].label}
        </Link>
      ))}
    </div>
  );

  return (
    <>
      <PageHeader
        icon={item.icon}
        tone={item.tone}
        title="Painel"
        description={`${formatDate(summary.period.start)} a ${formatDate(summary.period.end)}`}
      />
      {periodFilter}

      {summary.count === 0 && summary.open_bills.length === 0 ? (
        <EmptyState icon={LayoutDashboard} title="Nenhum gasto neste período">
          <span className="mt-3 block">
            <Link href={`${basePath}/documentos`} className={buttonClass.primary}>
              <FileUp size={16} aria-hidden /> Enviar documentos
            </Link>
          </span>
        </EmptyState>
      ) : (
        <div className="space-y-6">
          {summary.pending_review > 0 && (
            <Alert tone="info">
              {summary.pending_review === 1 ? "1 nota aguarda" : `${summary.pending_review} notas aguardam`} revisão e
              ainda não {summary.pending_review === 1 ? "entrou" : "entraram"} nos totais.{" "}
              <Link href={`${basePath}/documentos`} className="font-medium underline">
                Revisar em Documentos
              </Link>
            </Alert>
          )}

          {/* Key numbers */}
          <div className="grid gap-4 sm:grid-cols-3">
            <Card className="sm:col-span-1">
              <p className="text-sm text-slate-500">Gasto em {formatMonth(currentMonth)}</p>
              <p className="text-4xl font-semibold tracking-tight text-slate-900 sm:text-5xl">{formatBRL(currentTotal)}</p>
              {delta !== null ? (
                <p className={clsx("mt-2 inline-flex items-center gap-1 text-sm", delta > 0 ? "text-rose-700" : "text-emerald-700")}>
                  {delta > 0 ? <ArrowUpRight size={16} aria-hidden /> : <ArrowDownRight size={16} aria-hidden />}
                  {delta > 0 ? "+" : ""}
                  {delta}% vs. {formatMonth(previousMonth(currentMonth))}
                </p>
              ) : (
                <p className="mt-2 text-sm text-slate-500">Sem gastos no mês anterior para comparar.</p>
              )}
            </Card>
            <Card>
              <p className="text-sm text-slate-500">Total no período</p>
              <p className="text-3xl font-semibold text-slate-900">{formatBRL(summary.total)}</p>
              <p className="mt-2 text-sm text-slate-500">
                {summary.count} {summary.count === 1 ? "lançamento" : "lançamentos"}
              </p>
            </Card>
            <Card>
              <p className="text-sm text-slate-500">Contas em aberto</p>
              <p className="text-3xl font-semibold text-slate-900">{summary.open_bills.length}</p>
              <p className="mt-2 text-sm text-slate-500">
                {summary.open_bills[0]?.due_date
                  ? `Próximo vencimento: ${formatDate(summary.open_bills[0].due_date)}`
                  : "Nada vencendo nos próximos 30 dias."}
              </p>
            </Card>
          </div>

          <Card>
            <h2 className="mb-4 font-semibold text-slate-900">Gastos por mês</h2>
            <ColumnChart title="Gastos por mês" data={monthData} highlight={currentMonth} />
          </Card>

          {summary.open_bills.length > 0 && (
            <Card>
              <h2 className="mb-3 flex items-center gap-2 font-semibold text-slate-900">
                <CalendarClock size={18} className="text-slate-500" aria-hidden /> Contas a pagar
              </h2>
              <ul className="divide-y divide-slate-100">
                {summary.open_bills.map((bill) => {
                  const days = bill.due_date ? daysUntil(bill.due_date, today) : null;
                  const overdue = days !== null && days < 0;
                  const soon = days !== null && days >= 0 && days <= 7;
                  return (
                    <li key={bill.id} className="flex items-center gap-3 py-2.5">
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-medium text-slate-800">{bill.establishment ?? bill.description}</p>
                        {/* State with icon + text, never color alone */}
                        <p className={clsx("inline-flex items-center gap-1 text-sm", overdue ? "text-rose-700" : soon ? "text-amber-700" : "text-slate-500")}>
                          {(overdue || soon) && <AlertCircle size={14} aria-hidden />}
                          {days === null
                            ? "Sem vencimento"
                            : overdue
                              ? `Vencida há ${-days} ${-days === 1 ? "dia" : "dias"} (${formatDate(bill.due_date)})`
                              : days === 0
                                ? "Vence hoje"
                                : `Vence em ${days} ${days === 1 ? "dia" : "dias"} (${formatDate(bill.due_date)})`}
                        </p>
                      </div>
                      <span className="shrink-0 font-semibold tabular-nums text-slate-900">{formatBRL(bill.amount)}</span>
                      {bill.document_id && (
                        <Link href={`${basePath}/documentos/${bill.document_id}`} className="shrink-0 text-sm text-sky-700 hover:underline">
                          ver
                        </Link>
                      )}
                    </li>
                  );
                })}
              </ul>
            </Card>
          )}

          {recent.items.length > 0 && (
            <Card>
              <h2 className="mb-3 font-semibold text-slate-900">Últimos lançamentos</h2>
              <ul className="divide-y divide-slate-100">
                {recent.items.map((entry) => (
                  <li key={entry.id} className="flex items-center gap-3 py-2.5">
                    <span className="w-20 shrink-0 text-sm text-slate-500">{formatDate(entry.occurred_at)}</span>
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium text-slate-800">{entry.establishment ?? entry.description}</p>
                      <p className="text-xs text-slate-500">
                        {entry.category?.name ?? "Sem categoria"}
                        {entry.needs_review && (
                          <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 font-medium text-amber-800">em revisão</span>
                        )}
                      </p>
                    </div>
                    <span className="shrink-0 font-semibold tabular-nums text-slate-900">{formatBRL(entry.amount)}</span>
                    {entry.document_id && (
                      <Link href={`${basePath}/documentos/${entry.document_id}`} className="shrink-0 text-sm text-sky-700 hover:underline">
                        ver
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <div className="grid gap-6 lg:grid-cols-2">
            <Card>
              <h2 className="mb-4 font-semibold text-slate-900">Por categoria</h2>
              <RankedBars title="Gastos por categoria" data={groups(summary.by_category)} total={total} />
            </Card>
            <Card>
              <h2 className="mb-4 font-semibold text-slate-900">Onde mais gastou</h2>
              <RankedBars title="Gastos por estabelecimento" data={groups(summary.by_establishment)} total={total} />
            </Card>
            {products.length > 0 && (
              <Card>
                <h2 className="font-semibold text-slate-900">Produtos em que mais gastou</h2>
                <p className="mb-4 text-xs text-slate-500">Somados pelo nome impresso na nota. Clique para perguntar ao assistente.</p>
                {/* No % of the total: the period total includes bills, which have no products. */}
                <RankedBars title="Produtos em que mais gastou" data={products} />
              </Card>
            )}
            <Card className={products.length > 0 ? undefined : "lg:col-span-2"}>
              <h2 className="mb-4 font-semibold text-slate-900">Por tipo de documento</h2>
              <RankedBars title="Gastos por tipo de documento" data={groups(summary.by_document_type)} total={total} />
            </Card>
          </div>
        </div>
      )}
    </>
  );
}
