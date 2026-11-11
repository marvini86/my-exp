import type { components } from "@/lib/api/schema";

type Schemas = components["schemas"];
export type ProcessingStatus = Schemas["ProcessingStatus"];
export type PaymentStatus = Schemas["PaymentStatus"];
export type EntryType = Schemas["EntryType"];

/** Same labels as the API (services/indexing.py: DOCUMENT_TYPE_LABELS). */
export const DOCUMENT_TYPE_LABELS: Record<string, string> = {
  nfce: "Cupom fiscal (NFC-e)",
  nfe: "Nota fiscal (NF-e)",
  nfse: "Nota fiscal de serviço (NFS-e)",
  grocery_receipt: "Cupom de supermercado",
  pharmacy_receipt: "Recibo de farmácia",
  energy_bill: "Conta de energia",
  water_bill: "Conta de água",
  telecom_bill: "Conta de telefone/internet",
  bank_slip: "Boleto",
  payment_receipt: "Comprovante de pagamento",
  other: "Documento",
};

export function documentTypeLabel(type: string | null | undefined): string {
  if (!type) return "Identificando…";
  return DOCUMENT_TYPE_LABELS[type] ?? "Documento";
}

/** Label + badge classes (literal classes for Tailwind). */
export const PROCESSING_STATUS: Record<ProcessingStatus, { label: string; badge: string }> = {
  pending: { label: "Na fila", badge: "bg-slate-100 text-slate-700" },
  processing: { label: "Processando", badge: "bg-sky-100 text-sky-800" },
  completed: { label: "Concluído", badge: "bg-emerald-100 text-emerald-800" },
  failed: { label: "Falhou", badge: "bg-rose-100 text-rose-800" },
};

export const PAYMENT_STATUS: Record<PaymentStatus, { label: string; badge: string }> = {
  unknown: { label: "—", badge: "bg-slate-100 text-slate-600" },
  pending: { label: "A pagar", badge: "bg-amber-100 text-amber-800" },
  paid: { label: "Pago", badge: "bg-emerald-100 text-emerald-800" },
  overdue: { label: "Vencido", badge: "bg-rose-100 text-rose-800" },
  cancelled: { label: "Cancelado", badge: "bg-slate-100 text-slate-500" },
};

export const ENTRY_TYPE_LABELS: Record<EntryType, string> = {
  expense: "Despesa",
  income: "Receita",
  refund: "Estorno",
  transfer: "Transferência",
};

/** Document badge, accounting for extraction postponed for lack of AI quota. */
export function documentStatus(doc: { processing_status: ProcessingStatus; next_attempt_at?: string | null }) {
  if (doc.processing_status === "pending" && doc.next_attempt_at) {
    return { label: "Aguardando IA", badge: "bg-amber-100 text-amber-800" };
  }
  return PROCESSING_STATUS[doc.processing_status];
}

/** "21:00" in Brasília time. */
export function formatTime(iso: string): string {
  return new Intl.DateTimeFormat("pt-BR", { timeZone: "America/Sao_Paulo", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
}

/** Document still in progress in the worker (the screen refreshes on its own). */
export function isInProgress(status: ProcessingStatus): boolean {
  return status === "pending" || status === "processing";
}

export function formatFileSize(bytes: number | null | undefined): string {
  if (!bytes) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} MB`;
}
