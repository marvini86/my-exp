import clsx from "clsx";
import type { Metadata } from "next";
import { ArrowLeft, Download, FileText, Loader2, ReceiptText } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { AutoRefresh } from "@/components/AutoRefresh";
import { Alert, Card, EmptyState, buttonClass } from "@/components/ui";
import { api, unwrap } from "@/lib/api/server";
import { formatBRL, formatDate, formatDateTime } from "@/lib/format";
import { isUuid } from "@/lib/ids";
import {
  ENTRY_TYPE_LABELS,
  PAYMENT_STATUS,
  documentStatus,
  documentTypeLabel,
  formatFileSize,
  formatTime,
  isInProgress,
  reviewReasonText,
} from "@/lib/labels";
import { MANAGERS, hasRole } from "@/lib/roles";
import { getAccount, getCurrentUser } from "@/lib/session";
import { DocumentActions } from "../_components/DocumentActions";

export const metadata: Metadata = { title: "Documento" };

function formatQuantity(quantity: string | null, unit: string | null): string {
  if (!quantity) return "—";
  const n = Number(quantity);
  const text = Number.isFinite(n) ? n.toLocaleString("pt-BR", { maximumFractionDigits: 3 }) : quantity;
  return unit ? `${text} ${unit}` : text;
}

export default async function DocumentPage({ params }: { params: Promise<{ accountId: string; documentId: string }> }) {
  const { accountId, documentId } = await params;
  if (!isUuid(documentId)) notFound();
  const account = await getAccount(accountId);

  const doc = await unwrap(
    (await api()).GET("/accounts/{account_id}/documents/{document_id}", {
      params: { path: { account_id: account.id, document_id: documentId } },
    }),
  );

  const basePath = `/c/${account.id}/documentos`;
  const status = documentStatus(doc);
  const waitingQuota = doc.processing_status === "pending" && Boolean(doc.next_attempt_at);
  const working = isInProgress(doc.processing_status) && !waitingQuota;
  const user = await getCurrentUser();
  const canDelete = doc.processing_status !== "processing" && (doc.uploaded_by === user.id || hasRole(account.role, MANAGERS));
  const canReprocess = doc.processing_status === "failed" || doc.processing_status === "completed";

  return (
    <div className="mx-auto max-w-4xl">
      <AutoRefresh active={working} />

      <Link href={basePath} className={`${buttonClass.ghost} -ml-3 mb-4`}>
        <ArrowLeft size={16} aria-hidden /> Documentos
      </Link>

      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex min-w-0 items-start gap-3">
          <div className="rounded-xl bg-sky-100 p-3 text-sky-700">
            <FileText size={24} aria-hidden />
          </div>
          <div className="min-w-0">
            <h1 className="break-words text-xl font-bold text-slate-900 sm:text-2xl">{doc.original_filename}</h1>
            <p className="text-sm text-slate-500">
              {documentTypeLabel(doc.document_type)} · {formatFileSize(doc.file_size)} · enviado em {formatDateTime(doc.created_at)}
            </p>
          </div>
        </div>
        {/* Next route that asks the API for a temporary URL and redirects. */}
        <a href={`${basePath}/${doc.id}/download`} className={buttonClass.secondary} target="_blank" rel="noopener noreferrer">
          <Download size={16} aria-hidden /> Abrir original
        </a>
      </div>

      <div className="mb-6 flex flex-wrap items-center gap-2 text-sm">
        <span className={clsx("rounded-full px-2.5 py-0.5 font-medium", status.badge)}>{status.label}</span>
        {doc.processing_status === "completed" && (
          <span className="text-slate-500">
            {doc.indexed_at ? "Disponível para o assistente e a busca." : "Preparando para o assistente…"}
          </span>
        )}
      </div>

      {doc.duplicate_of && (
        <div className="mb-6">
          <Alert tone="info">
            Esta nota já tinha sido enviada (mesma chave de acesso), então não gerou uma nova despesa.{" "}
            <Link href={`${basePath}/${doc.duplicate_of}`} className="font-medium underline">
              Ver a nota original
            </Link>
          </Alert>
        </div>
      )}

      {doc.needs_review && (
        <div className="mb-6 rounded-xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-semibold">Confira os valores desta nota</p>
          <p className="mt-1">A leitura ficou com dúvidas, por isso ela ainda não entrou nos totais nem nas respostas do assistente:</p>
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {doc.review_reasons.map((reason, i) => (
              <li key={i}>
                {reviewReasonText(reason)}
                {reason.code === "possible_duplicate" && reason.document_id && (
                  <>
                    {" "}
                    <Link href={`${basePath}/${reason.document_id}`} className="font-medium underline">
                      Ver a outra nota
                    </Link>
                  </>
                )}
              </li>
            ))}
          </ul>
          <p className="mt-2">Se os valores abaixo estiverem certos, confirme. Se não, processe de novo ou apague.</p>
        </div>
      )}

      <DocumentActions
        accountId={account.id}
        documentId={doc.id}
        canReprocess={canReprocess}
        canDelete={canDelete}
        canConfirm={doc.needs_review && doc.processing_status === "completed"}
      />

      {waitingQuota && doc.next_attempt_at && (
        <div className="mb-6">
          <Alert tone="info">
            O limite diário da IA gratuita foi atingido. Este documento será lido automaticamente às{" "}
            {formatTime(doc.next_attempt_at)}; não precisa enviar de novo.
          </Alert>
        </div>
      )}

      {working && (
        <div className="mb-6">
          <Alert tone="info">
            <span className="inline-flex items-center gap-2">
              <Loader2 size={16} className="animate-spin" aria-hidden />
              Lendo o documento e extraindo os valores. Esta página se atualiza sozinha.
            </span>
          </Alert>
        </div>
      )}

      {doc.processing_status === "failed" && (
        <div className="mb-6">
          <Alert>
            Não foi possível extrair as informações deste documento. Confira se o arquivo está legível (foto nítida,
            PDF não protegido) e envie novamente.
          </Alert>
        </div>
      )}

      {!working && !waitingQuota && (
        <section className="mb-6" aria-labelledby="lancamentos">
          <h2 id="lancamentos" className="mb-3 text-lg font-semibold text-slate-900">
            Lançamentos
          </h2>
          {doc.entries.length === 0 ? (
            <EmptyState icon={ReceiptText} title="Nenhum lançamento extraído" />
          ) : (
            <div className="grid gap-3 sm:grid-cols-2">
              {doc.entries.map((entry) => {
                const payment = PAYMENT_STATUS[entry.payment_status];
                return (
                  <Card key={entry.id}>
                    <p className="text-sm text-slate-500">{ENTRY_TYPE_LABELS[entry.entry_type]}</p>
                    <p className="text-2xl font-bold text-slate-900">{formatBRL(entry.amount)}</p>
                    <p className="mt-1 font-medium text-slate-800">{entry.establishment ?? entry.description}</p>
                    {entry.establishment && <p className="text-sm text-slate-500">{entry.description}</p>}
                    <dl className="mt-3 grid grid-cols-2 gap-2 text-sm">
                      <div>
                        <dt className="text-slate-500">Data</dt>
                        <dd className="text-slate-800">{formatDate(entry.occurred_at)}</dd>
                      </div>
                      {entry.due_date && (
                        <div>
                          <dt className="text-slate-500">Vencimento</dt>
                          <dd className="text-slate-800">{formatDate(entry.due_date)}</dd>
                        </div>
                      )}
                      {entry.payment_status !== "unknown" && (
                        <div>
                          <dt className="text-slate-500">Pagamento</dt>
                          <dd>
                            <span className={clsx("rounded-full px-2 py-0.5 text-xs font-medium", payment.badge)}>{payment.label}</span>
                          </dd>
                        </div>
                      )}
                      <div>
                        <dt className="text-slate-500">Categoria</dt>
                        <dd className="text-slate-800">{entry.category?.name ?? "Sem categoria"}</dd>
                      </div>
                    </dl>
                  </Card>
                );
              })}
            </div>
          )}
        </section>
      )}

      {doc.items.length > 0 && (
        <section aria-labelledby="itens">
          <h2 id="itens" className="mb-3 text-lg font-semibold text-slate-900">
            Itens ({doc.items.length})
          </h2>
          <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white">
            <table className="w-full text-sm">
              <thead className="bg-slate-50 text-left text-slate-500">
                <tr>
                  <th scope="col" className="px-4 py-2 font-medium">Produto</th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">Qtd.</th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">Unitário</th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">Total</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {doc.items.map((item) => (
                  <tr key={item.id}>
                    <td className="px-4 py-2 text-slate-800">
                      {item.description}
                      {item.brand && <span className="block text-xs text-slate-500">{item.brand}</span>}
                    </td>
                    <td className="whitespace-nowrap px-4 py-2 text-right text-slate-600">{formatQuantity(item.quantity, item.unit)}</td>
                    <td className="whitespace-nowrap px-4 py-2 text-right text-slate-600">{formatBRL(item.unit_price)}</td>
                    <td className="whitespace-nowrap px-4 py-2 text-right font-medium text-slate-800">{formatBRL(item.total_price)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
