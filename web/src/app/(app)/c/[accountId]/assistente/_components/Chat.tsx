"use client";

import clsx from "clsx";
import { Bot, CheckCircle2, Clock, FileText, Loader2, Paperclip, RotateCcw, Send, XCircle } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useTransition } from "react";

import { uploadDocument } from "../../documentos/actions";
import { AnswerText } from "@/lib/answer";
import { askAssistant, attachmentStatus, type AttachmentStatus, type ChatMessage } from "../actions";

const SUGGESTIONS = [
  "Quanto gastei este mês?",
  "Onde comprei leite mais barato?",
  "Qual foi minha última conta de luz e quando vence?",
  "Quais foram meus maiores gastos no mês passado?",
];

const ACCEPT = ".pdf,.xml,.jpg,.jpeg,.png,application/pdf,application/xml,text/xml,image/jpeg,image/png";
const POLL_MS = 3000;
const POLL_LIMIT = 80; // ~4 min; after that the card points to the Documents screen

type Item =
  | { kind: "message"; message: ChatMessage }
  | { kind: "pending"; id: string; question: string }
  | { kind: "error"; id: string; question: string; message: string; retryable: boolean }
  | { kind: "attachment"; id: string; filename: string; documentId?: string; status: AttachmentStatus | { state: "sending" } | { state: "rejected"; message: string } };

function citationLinks(accountId: string, message: ChatMessage) {
  return message.citations.map((c) => ({
    ref: c.ref,
    href: `/c/${accountId}/documentos/${c.document_id}`,
    label: c.original_filename,
  }));
}

export function Chat({
  accountId,
  conversationId: initialConversationId,
  initialMessages,
  initialDraft = "",
}: {
  accountId: string;
  conversationId: string | null;
  initialMessages: ChatMessage[];
  initialDraft?: string;
}) {
  const router = useRouter();
  const [conversationId, setConversationId] = useState(initialConversationId);
  const [items, setItems] = useState<Item[]>(initialMessages.map((message) => ({ kind: "message", message })));
  const [draft, setDraft] = useState(initialDraft);
  const [isPending, startTransition] = useTransition();
  const fileInput = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  // Braces required: scrollIntoView returns a Promise in newer browsers,
  // and an effect may only return the cleanup function.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [items.length]);

  function ask(question: string, replaceId?: string) {
    const text = question.trim();
    if (text.length < 2 || isPending) return;
    const id = replaceId ?? crypto.randomUUID();
    setItems((current) => [
      ...current.filter((item) => !("id" in item) || item.id !== id),
      { kind: "pending", id, question: text },
    ]);
    setDraft("");

    startTransition(async () => {
      const result = await askAssistant(accountId, conversationId, text);
      if (result.conversationId && result.conversationId !== conversationId) {
        setConversationId(result.conversationId);
        // New conversation: its own URL (without reloading the chat) and an updated side list.
        window.history.replaceState(null, "", `/c/${accountId}/assistente/${result.conversationId}`);
        router.refresh();
      }
      setItems((current) =>
        current.flatMap((item): Item[] => {
          if (!("id" in item) || item.id !== id) return [item];
          if (result.ok) {
            return [
              { kind: "message", message: result.user },
              { kind: "message", message: result.assistant },
            ];
          }
          return [{ kind: "error", id, question: text, message: result.message, retryable: result.retryable }];
        }),
      );
    });
  }

  function attach(files: FileList | null) {
    for (const file of Array.from(files ?? [])) {
      const id = crypto.randomUUID();
      setItems((current) => [...current, { kind: "attachment", id, filename: file.name, status: { state: "sending" } }]);
      const update = (patch: Partial<Extract<Item, { kind: "attachment" }>>) =>
        setItems((current) => current.map((item) => (item.kind === "attachment" && item.id === id ? { ...item, ...patch } : item)));

      (async () => {
        const form = new FormData();
        form.append("file", file);
        const sent = await uploadDocument(accountId, form).catch(() => null);
        if (!sent || !sent.ok) {
          update({ status: { state: "rejected", message: sent?.message ?? "Falha de conexão." } });
          return;
        }
        update({ documentId: sent.id, status: { state: "working" } });
        for (let i = 0; i < POLL_LIMIT; i++) {
          await new Promise((resolve) => setTimeout(resolve, POLL_MS));
          const status = await attachmentStatus(accountId, sent.id).catch(() => null);
          if (status && status.state !== "working") {
            update({ status });
            return;
          }
        }
      })();
    }
  }

  const empty = items.length === 0;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto pb-4" aria-live="polite">
        {empty ? (
          <div className="mx-auto flex max-w-xl flex-col items-center pt-10 text-center">
            <div className="mb-3 rounded-2xl bg-violet-100 p-3 text-violet-700">
              <Bot size={28} aria-hidden />
            </div>
            <h1 className="text-xl font-semibold text-slate-900">Pergunte sobre seus gastos</h1>
            <p className="mt-1 text-sm text-slate-500">
              As respostas usam suas notas, contas e recibos, com link para o documento. Envie novos pelo clipe 📎.
            </p>
            <div className="mt-6 grid w-full gap-2 sm:grid-cols-2">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  onClick={() => ask(s)}
                  className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-left text-sm text-slate-700 transition hover:border-violet-300 hover:bg-violet-50"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <ol className="mx-auto max-w-3xl space-y-4">
            {items.map((item) => (
              <li key={item.kind === "message" ? item.message.id : item.id}>
                <ChatItem item={item} accountId={accountId} onRetry={(question, id) => ask(question, id)} />
              </li>
            ))}
          </ol>
        )}
        <div ref={endRef} />
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          ask(draft);
        }}
        className="mx-auto flex w-full max-w-3xl items-end gap-2 rounded-2xl border border-slate-300 bg-white p-2 shadow-sm focus-within:border-violet-400"
      >
        <button
          type="button"
          onClick={() => fileInput.current?.click()}
          className="rounded-lg p-2 text-slate-500 transition hover:bg-slate-100 hover:text-slate-800"
          aria-label="Enviar nota, conta ou foto"
          title="Enviar nota, conta ou foto"
        >
          <Paperclip size={20} aria-hidden />
        </button>
        <input
          ref={fileInput}
          type="file"
          multiple
          accept={ACCEPT}
          className="hidden"
          onChange={(event) => {
            attach(event.target.files);
            event.target.value = "";
          }}
        />
        <label htmlFor="question" className="sr-only">Pergunta</label>
        <textarea
          id="question"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends; Shift+Enter adds a line break.
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              ask(draft);
            }
          }}
          rows={1}
          maxLength={2000}
          placeholder="Pergunte algo, ex.: quanto gastei no mercado em outubro?"
          className="max-h-40 min-h-10 flex-1 resize-none border-0 bg-transparent px-1 py-2 text-slate-900 placeholder:text-slate-400 focus:outline-none field-sizing-content"
        />
        <button
          type="submit"
          disabled={isPending || draft.trim().length < 2}
          className="rounded-lg bg-violet-700 p-2 text-white transition hover:bg-violet-800 disabled:opacity-40"
          aria-label="Enviar pergunta"
        >
          {isPending ? <Loader2 size={20} className="animate-spin" aria-hidden /> : <Send size={20} aria-hidden />}
        </button>
      </form>
    </div>
  );
}

function ChatItem({ item, accountId, onRetry }: { item: Item; accountId: string; onRetry: (question: string, id: string) => void }) {
  if (item.kind === "message") {
    const { message } = item;
    if (message.role === "user") {
      return (
        <div className="flex justify-end">
          <p className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-violet-700 px-4 py-2.5 text-white">{message.content}</p>
        </div>
      );
    }
    return (
      <div className="flex gap-3">
        <span className="mt-1 h-fit rounded-lg bg-violet-100 p-1.5 text-violet-700" aria-hidden>
          <Bot size={16} />
        </span>
        <div className="min-w-0 max-w-[90%] rounded-2xl rounded-tl-sm border border-slate-200 bg-white px-4 py-3 text-slate-800">
          <AnswerText text={message.content} citations={citationLinks(accountId, message)} />
          {message.citations.length > 0 && (
            <div className="mt-3 flex flex-wrap gap-2 border-t border-slate-100 pt-2">
              {message.citations.map((c) => (
                <Link
                  key={c.ref}
                  href={`/c/${accountId}/documentos/${c.document_id}`}
                  className="inline-flex max-w-full items-center gap-1 rounded-md bg-slate-50 px-2 py-1 text-xs text-slate-600 ring-1 ring-slate-200 hover:bg-violet-50 hover:text-violet-800"
                >
                  <span className="font-semibold text-violet-700">{c.ref.slice(1)}</span>
                  <FileText size={12} aria-hidden />
                  <span className="truncate">{c.original_filename}</span>
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (item.kind === "pending" || item.kind === "error") {
    return (
      <>
        <div className="flex justify-end">
          <p className={clsx("max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm px-4 py-2.5 text-white", item.kind === "pending" ? "bg-violet-700/80" : "bg-violet-700")}>
            {item.question}
          </p>
        </div>
        <div className="mt-4 flex gap-3">
          <span className="mt-1 h-fit rounded-lg bg-violet-100 p-1.5 text-violet-700" aria-hidden>
            <Bot size={16} />
          </span>
          {item.kind === "pending" ? (
            <p className="inline-flex items-center gap-2 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-500">
              <Loader2 size={16} className="animate-spin" aria-hidden /> Consultando seus documentos…
            </p>
          ) : (
            <div role="alert" className="rounded-2xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-800">
              {item.message}
              {item.retryable && (
                <button type="button" onClick={() => onRetry(item.question, item.id)} className="ml-2 inline-flex items-center gap-1 font-medium underline">
                  <RotateCcw size={14} aria-hidden /> Tentar de novo
                </button>
              )}
            </div>
          )}
        </div>
      </>
    );
  }

  // Attachment sent through the chat
  const { status } = item;
  return (
    <div className="flex justify-end">
      <div className="flex max-w-[85%] items-start gap-3 rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm">
        {status.state === "done" ? (
          <CheckCircle2 size={18} className="mt-0.5 shrink-0 text-emerald-600" aria-hidden />
        ) : status.state === "waiting" ? (
          <Clock size={18} className="mt-0.5 shrink-0 text-amber-600" aria-hidden />
        ) : status.state === "failed" || status.state === "rejected" ? (
          <XCircle size={18} className="mt-0.5 shrink-0 text-rose-600" aria-hidden />
        ) : (
          <Loader2 size={18} className="mt-0.5 shrink-0 animate-spin text-sky-600" aria-hidden />
        )}
        <div className="min-w-0">
          <p className="truncate font-medium text-slate-800">{item.filename}</p>
          <p className="text-slate-500">
            {status.state === "sending" && "Enviando…"}
            {status.state === "working" && "Lendo o documento…"}
            {status.state === "waiting" && `Guardado. A IA gratuita atingiu o limite do dia; leio às ${status.at}.`}
            {status.state === "rejected" && <span className="text-rose-700">{status.message}</span>}
            {status.state === "failed" && <span className="text-rose-700">Não consegui ler este documento.</span>}
            {status.state === "done" && (
              <>
                <span className="font-medium text-slate-700">Registrei: </span>
                {status.title} — {status.summary}
              </>
            )}
          </p>
          {item.documentId && (
            <Link href={`/c/${accountId}/documentos/${item.documentId}`} className="text-xs text-sky-700 hover:underline">
              ver documento
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
