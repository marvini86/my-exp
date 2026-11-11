"use server";

import { revalidatePath } from "next/cache";

import { api, attempt } from "@/lib/api/server";
import type { components } from "@/lib/api/schema";
import { whenAvailable } from "@/lib/errors";
import { formatBRL, formatDate } from "@/lib/format";
import { isUuid } from "@/lib/ids";
import { documentTypeLabel, formatTime } from "@/lib/labels";

export type ChatMessage = components["schemas"]["MessageResponse"];

export type AskResult =
  | { ok: true; conversationId: string; user: ChatMessage; assistant: ChatMessage }
  | { ok: false; conversationId?: string; message: string; retryable: boolean };

const MAX_QUESTION = 2000;

/** Asks a question. No conversation yet → creates one (the title comes from the 1st question, in the API). */
export async function askAssistant(accountId: string, conversationId: string | null, content: string): Promise<AskResult> {
  const question = content.trim();
  if (!isUuid(accountId) || (conversationId !== null && !isUuid(conversationId))) {
    return { ok: false, message: "Conversa inválida.", retryable: false };
  }
  if (question.length < 2) return { ok: false, message: "Escreva uma pergunta.", retryable: false };
  if (question.length > MAX_QUESTION) return { ok: false, message: "Pergunta muito longa (máx. 2000 caracteres).", retryable: false };

  const client = await api();
  let id = conversationId;
  if (!id) {
    const created = await attempt(
      client.POST("/accounts/{account_id}/conversations/", { params: { path: { account_id: accountId } }, body: {} }),
    );
    if (created.error) return { ok: false, message: created.error.message, retryable: true };
    id = created.data.id;
  }

  const result = await attempt(
    client.POST("/accounts/{account_id}/conversations/{conversation_id}/messages", {
      params: { path: { account_id: accountId, conversation_id: id } },
      body: { content: question },
    }),
  );

  revalidatePath(`/c/${accountId}/assistente`, "layout");

  if (result.error) {
    const { status, retryAfter } = result.error;
    const dailyLimit = status === 503 && (retryAfter ?? 0) >= 3600;
    if (dailyLimit) {
      return {
        ok: false,
        conversationId: id,
        message: `O limite diário da IA gratuita foi atingido. Sua pergunta ficou salva; tente de novo ${whenAvailable(retryAfter!)}.`,
        retryable: false,
      };
    }
    return {
      ok: false,
      conversationId: id,
      message:
        status === 503
          ? "Os modelos de IA gratuitos estão ocupados agora. Sua pergunta ficou salva; tente de novo em instantes."
          : result.error.message,
      retryable: status === 503 || status === 429,
    };
  }
  return { ok: true, conversationId: id, user: result.data.user_message, assistant: result.data.assistant_message };
}

export type AttachmentStatus =
  | { state: "working" }
  | { state: "waiting"; at: string }
  | { state: "failed" }
  | { state: "done"; title: string; summary: string };

/** Status of a document sent through the chat (the card polls until it finishes). */
export async function attachmentStatus(accountId: string, documentId: string): Promise<AttachmentStatus> {
  if (!isUuid(accountId) || !isUuid(documentId)) return { state: "failed" };
  const result = await attempt(
    (await api()).GET("/accounts/{account_id}/documents/{document_id}", {
      params: { path: { account_id: accountId, document_id: documentId } },
    }),
  );
  if (result.error) return { state: "failed" };

  const doc = result.data;
  if (doc.processing_status === "failed") return { state: "failed" };
  if (doc.processing_status === "pending" && doc.next_attempt_at) return { state: "waiting", at: formatTime(doc.next_attempt_at) };
  if (doc.processing_status !== "completed") return { state: "working" };

  const entry = doc.entries[0];
  return {
    state: "done",
    title: documentTypeLabel(doc.document_type),
    summary: entry
      ? [entry.establishment, formatBRL(entry.amount), formatDate(entry.occurred_at), entry.category?.name].filter(Boolean).join(" · ")
      : "Documento guardado, sem valores para registrar.",
  };
}
