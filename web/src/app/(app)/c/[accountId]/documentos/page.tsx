import clsx from "clsx";
import type { Metadata } from "next";
import { FileText } from "lucide-react";
import Link from "next/link";

import { AutoRefresh } from "@/components/AutoRefresh";
import { Pagination, pageFromParam } from "@/components/Pagination";
import { navItem } from "@/components/shell/nav";
import { EmptyState, PageHeader } from "@/components/ui";
import { api, unwrap } from "@/lib/api/server";
import { formatDateTime } from "@/lib/format";
import {
  PROCESSING_STATUS,
  documentStatus,
  documentTypeLabel,
  formatFileSize,
  formatTime,
  isInProgress,
  type ProcessingStatus,
} from "@/lib/labels";
import { getAccount } from "@/lib/session";
import { UploadPanel } from "./_components/UploadPanel";

export const metadata: Metadata = { title: "Documentos" };

const PAGE_SIZE = 20;

const FILTERS: { value?: ProcessingStatus; label: string }[] = [
  { label: "Todos" },
  { value: "pending", label: "Na fila" },
  { value: "processing", label: "Processando" },
  { value: "completed", label: "Concluídos" },
  { value: "failed", label: "Com falha" },
];

function parseStatus(value: string | string[] | undefined): ProcessingStatus | undefined {
  const v = Array.isArray(value) ? value[0] : value;
  return v && v in PROCESSING_STATUS ? (v as ProcessingStatus) : undefined;
}

export default async function DocumentsPage({
  params,
  searchParams,
}: {
  params: Promise<{ accountId: string }>;
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const { accountId } = await params;
  const query = await searchParams;
  const page = pageFromParam(query.pagina);
  const status = parseStatus(query.status);
  const account = await getAccount(accountId);

  const data = await unwrap(
    (await api()).GET("/accounts/{account_id}/documents/", {
      params: {
        path: { account_id: account.id },
        query: { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE, status },
      },
    }),
  );

  const basePath = `/c/${account.id}/documentos`;
  const item = navItem("documentos");
  const working = data.items.some((d) => isInProgress(d.processing_status) && !d.next_attempt_at);

  return (
    <>
      <AutoRefresh active={working} />
      <PageHeader icon={item.icon} tone={item.tone} title="Documentos" description="Envie e acompanhe notas, contas e recibos." />

      <UploadPanel accountId={account.id} />

      <div className="mb-4 flex flex-wrap gap-2" aria-label="Filtrar por status">
        {FILTERS.map((filter) => {
          const active = filter.value === status;
          const href = filter.value ? `${basePath}?status=${filter.value}` : basePath;
          return (
            <Link
              key={filter.label}
              href={href}
              aria-current={active ? "page" : undefined}
              className={clsx(
                "rounded-full px-3 py-1 text-sm font-medium transition",
                active ? "bg-sky-700 text-white" : "bg-white text-slate-600 ring-1 ring-slate-200 hover:bg-slate-50",
              )}
            >
              {filter.label}
            </Link>
          );
        })}
      </div>

      {data.items.length === 0 ? (
        <EmptyState icon={FileText} title={status ? "Nenhum documento com esse status" : "Nenhum documento ainda"}>
          {status ? "Tente outro filtro." : "Envie a primeira nota ou conta acima."}
        </EmptyState>
      ) : (
        <>
          <ul className="divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white">
            {data.items.map((doc) => {
              const st = documentStatus(doc);
              return (
                <li key={doc.id}>
                  <Link href={`${basePath}/${doc.id}`} className="flex items-center gap-4 px-4 py-3 transition hover:bg-slate-50">
                    <FileText size={20} className="shrink-0 text-sky-600" aria-hidden />
                    <div className="min-w-0 flex-1">
                      <p className="truncate font-medium text-slate-800">{doc.original_filename}</p>
                      <p className="text-sm text-slate-500">
                        {documentTypeLabel(doc.document_type)} · {formatDateTime(doc.created_at)} · {formatFileSize(doc.file_size)}
                        {doc.next_attempt_at && <> · processa às {formatTime(doc.next_attempt_at)}</>}
                      </p>
                    </div>
                    <span className={clsx("shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium", st.badge)}>{st.label}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
          <Pagination basePath={basePath} page={page} pageSize={PAGE_SIZE} total={data.total} params={{ status }} />
        </>
      )}
    </>
  );
}
