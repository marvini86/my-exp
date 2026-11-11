"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { api, attempt } from "@/lib/api/server";
import { isUuid } from "@/lib/ids";

export type UploadResult =
  | { ok: true; id: string; filename: string }
  | { ok: false; filename: string; message: string; duplicate?: boolean };

/** Limits mirror the API (UPLOAD_MAX_BYTES = 10 MB); it still validates. */
const MAX_BYTES = 10 * 1024 * 1024;

export async function uploadDocument(accountId: string, form: FormData): Promise<UploadResult> {
  const file = form.get("file");
  const filename = file instanceof File ? file.name : "arquivo";

  if (!isUuid(accountId)) return { ok: false, filename, message: "Conta inválida." };
  if (!(file instanceof File) || file.size === 0) return { ok: false, filename, message: "Arquivo vazio." };
  if (file.size > MAX_BYTES) return { ok: false, filename, message: "Arquivo maior que 10 MB." };

  const body = new FormData();
  body.append("file", file, file.name);

  const result = await attempt(
    (await api()).POST("/accounts/{account_id}/documents/", {
      params: { path: { account_id: accountId } },
      // multipart: the FormData goes as is (fetch sets the boundary).
      body: body as unknown as { file: string },
      bodySerializer: (value) => value as unknown as FormData,
    }),
  );

  if (result.error) {
    return {
      ok: false,
      filename,
      message: result.error.message,
      duplicate: result.error.status === 409,
    };
  }

  revalidatePath(`/c/${accountId}/documentos`);
  return { ok: true, id: result.data.id, filename };
}

export type DocumentActionState = { message?: string };

function ids(form: FormData): { accountId: string; documentId: string } | null {
  const accountId = form.get("accountId");
  const documentId = form.get("documentId");
  return isUuid(accountId) && isUuid(documentId) ? { accountId, documentId } : null;
}

/** Puts it back in the extraction queue. */
export async function reprocessDocument(_: DocumentActionState, form: FormData): Promise<DocumentActionState> {
  const target = ids(form);
  if (!target) return { message: "Documento inválido." };
  const result = await attempt(
    (await api()).POST("/accounts/{account_id}/documents/{document_id}/reprocess", {
      params: { path: { account_id: target.accountId, document_id: target.documentId } },
    }),
  );
  if (result.error) return { message: result.error.message };
  revalidatePath(`/c/${target.accountId}/documentos`, "layout");
  return {};
}

/** Deletes the document and everything derived from it; goes back to the list. */
export async function deleteDocument(_: DocumentActionState, form: FormData): Promise<DocumentActionState> {
  const target = ids(form);
  if (!target) return { message: "Documento inválido." };
  const result = await attempt(
    (await api()).DELETE("/accounts/{account_id}/documents/{document_id}", {
      params: { path: { account_id: target.accountId, document_id: target.documentId } },
    }),
  );
  if (result.error) return { message: result.error.message };
  revalidatePath(`/c/${target.accountId}`, "layout");
  redirect(`/c/${target.accountId}/documentos`);
}
