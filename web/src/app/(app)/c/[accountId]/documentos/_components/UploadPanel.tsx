"use client";

import clsx from "clsx";
import { Camera, CheckCircle2, FileUp, Loader2, XCircle } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState, useTransition } from "react";

import { buttonClass } from "@/components/ui";
import { uploadDocument, type UploadResult } from "../actions";

const ACCEPT = ".pdf,.xml,.jpg,.jpeg,.png,application/pdf,application/xml,text/xml,image/jpeg,image/png";

type Row = { key: string; filename: string; state: "sending" } | ({ key: string } & UploadResult);

/**
 * Document upload: drag and drop, pick files or take a photo
 * (opens the camera on mobile). Uploads one at a time and shows each result.
 */
export function UploadPanel({ accountId }: { accountId: string }) {
  const router = useRouter();
  const fileInput = useRef<HTMLInputElement>(null);
  const cameraInput = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [rows, setRows] = useState<Row[]>([]);
  const [isPending, startTransition] = useTransition();

  function send(files: FileList | File[] | null) {
    const list = Array.from(files ?? []);
    if (!list.length) return;

    const batch = list.map((file, i) => ({ key: `${Date.now()}-${i}`, file }));
    setRows((current) => [
      ...batch.map(({ key, file }) => ({ key, filename: file.name, state: "sending" as const })),
      ...current,
    ]);

    startTransition(async () => {
      for (const { key, file } of batch) {
        const form = new FormData();
        form.append("file", file);
        let result: UploadResult;
        try {
          result = await uploadDocument(accountId, form);
        } catch {
          result = { ok: false, filename: file.name, message: "Falha de conexão. Tente novamente." };
        }
        setRows((current) => current.map((row) => (row.key === key ? { key, ...result } : row)));
      }
      router.refresh();
    });
  }

  return (
    <div className="mb-6 space-y-3">
      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          send(event.dataTransfer.files);
        }}
        className={clsx(
          "flex flex-col items-center gap-3 rounded-xl border-2 border-dashed px-6 py-8 text-center transition",
          dragging ? "border-sky-500 bg-sky-50" : "border-slate-300 bg-white",
        )}
      >
        <FileUp size={32} className="text-sky-600" aria-hidden />
        <div>
          <p className="font-medium text-slate-800">Arraste notas, contas ou recibos aqui</p>
          <p className="text-sm text-slate-500">PDF, foto (JPG/PNG) ou XML da nota fiscal · até 10 MB</p>
        </div>
        <div className="flex flex-wrap justify-center gap-2">
          <button type="button" onClick={() => fileInput.current?.click()} className={buttonClass.primary} disabled={isPending}>
            <FileUp size={16} aria-hidden />
            Escolher arquivos
          </button>
          <button type="button" onClick={() => cameraInput.current?.click()} className={buttonClass.secondary} disabled={isPending}>
            <Camera size={16} aria-hidden />
            Tirar foto
          </button>
        </div>
        <input
          ref={fileInput}
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          aria-label="Escolher arquivos"
          onChange={(event) => {
            send(event.target.files);
            event.target.value = "";
          }}
        />
        <input
          ref={cameraInput}
          type="file"
          accept="image/jpeg,image/png"
          capture="environment"
          className="hidden"
          aria-label="Tirar foto"
          onChange={(event) => {
            send(event.target.files);
            event.target.value = "";
          }}
        />
      </div>

      {rows.length > 0 && (
        <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white" aria-live="polite">
          {rows.map((row) => (
            <li key={row.key} className="flex items-center gap-3 px-4 py-2.5 text-sm">
              {"state" in row ? (
                <Loader2 size={18} className="shrink-0 animate-spin text-sky-600" aria-hidden />
              ) : row.ok ? (
                <CheckCircle2 size={18} className="shrink-0 text-emerald-600" aria-hidden />
              ) : (
                <XCircle size={18} className="shrink-0 text-rose-600" aria-hidden />
              )}
              <span className="min-w-0 flex-1 truncate font-medium text-slate-700">{row.filename}</span>
              <span className="shrink-0 text-slate-500">
                {"state" in row ? (
                  "Enviando…"
                ) : row.ok ? (
                  <Link href={`/c/${accountId}/documentos/${row.id}`} className="text-sky-700 hover:underline">
                    Enviado · ver
                  </Link>
                ) : (
                  <span className="text-rose-700">{row.message}</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
