"use client";

import { RotateCcw, Trash2 } from "lucide-react";
import { useActionState } from "react";

import { SubmitButton } from "@/components/SubmitButton";
import { Alert, buttonClass } from "@/components/ui";
import { deleteDocument, reprocessDocument, type DocumentActionState } from "../actions";

/** Reprocess / delete. Delete asks for confirmation (no modal: one more step on the page itself). */
export function DocumentActions({
  accountId,
  documentId,
  canReprocess,
  canDelete,
}: {
  accountId: string;
  documentId: string;
  canReprocess: boolean;
  canDelete: boolean;
}) {
  const [reprocessState, reprocess] = useActionState<DocumentActionState, FormData>(reprocessDocument, {});
  const [deleteState, remove] = useActionState<DocumentActionState, FormData>(deleteDocument, {});
  if (!canReprocess && !canDelete) return null;

  const hidden = (
    <>
      <input type="hidden" name="accountId" value={accountId} />
      <input type="hidden" name="documentId" value={documentId} />
    </>
  );

  return (
    <div className="mb-6 space-y-3">
      <div className="flex flex-wrap items-start gap-2">
        {canReprocess && (
          <form action={reprocess}>
            {hidden}
            <SubmitButton variant="secondary" pendingLabel="Enviando…">
              <RotateCcw size={16} aria-hidden /> Processar de novo
            </SubmitButton>
          </form>
        )}
        {canDelete && (
          <details className="group">
            <summary className={`${buttonClass.secondary} cursor-pointer list-none text-rose-700 hover:bg-rose-50`}>
              <Trash2 size={16} aria-hidden /> Apagar
            </summary>
            <form action={remove} className="mt-2 rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-900">
              {hidden}
              <p className="mb-2">Apagar o arquivo, os lançamentos e os itens deste documento? Não dá para desfazer.</p>
              <SubmitButton pendingLabel="Apagando…" className="!bg-rose-700 hover:!bg-rose-800">
                Sim, apagar
              </SubmitButton>
            </form>
          </details>
        )}
      </div>
      {reprocessState.message && <Alert>{reprocessState.message}</Alert>}
      {deleteState.message && <Alert>{deleteState.message}</Alert>}
    </div>
  );
}
