"use client";

import clsx from "clsx";
import { MessageSquare, Plus } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { formatDateTime } from "@/lib/format";

type ConversationSummary = { id: string; title: string | null; updated_at: string };

/** Previous conversations. On mobile it is collapsed so the chat takes the screen. */
export function ConversationList({ accountId, conversations }: { accountId: string; conversations: ConversationSummary[] }) {
  const pathname = usePathname();
  const base = `/c/${accountId}/assistente`;

  const list = (
    <nav aria-label="Conversas" className="space-y-1">
      <Link
        href={base}
        className="mb-2 flex items-center gap-2 rounded-lg bg-violet-700 px-3 py-2 text-sm font-medium text-white transition hover:bg-violet-800"
      >
        <Plus size={16} aria-hidden /> Nova conversa
      </Link>
      {conversations.length === 0 && <p className="px-3 py-2 text-sm text-slate-500">Nenhuma conversa ainda.</p>}
      {conversations.map((c) => {
        const href = `${base}/${c.id}`;
        const active = pathname === href;
        return (
          <Link
            key={c.id}
            href={href}
            aria-current={active ? "page" : undefined}
            className={clsx(
              "block rounded-lg px-3 py-2 text-sm transition",
              active ? "bg-violet-100 text-violet-900" : "text-slate-700 hover:bg-slate-100",
            )}
          >
            <span className="flex items-center gap-2">
              <MessageSquare size={14} className="shrink-0 text-slate-400" aria-hidden />
              <span className="truncate">{c.title ?? "Conversa sem título"}</span>
            </span>
            <span className="ml-6 block text-xs text-slate-400">{formatDateTime(c.updated_at)}</span>
          </Link>
        );
      })}
    </nav>
  );

  return (
    <>
      <aside className="hidden w-64 shrink-0 overflow-y-auto lg:block">{list}</aside>
      {/* Celular/tablet: recolhida no topo */}
      <details className="absolute right-0 top-0 z-20 lg:hidden">
        <summary className="flex cursor-pointer list-none items-center gap-1 rounded-lg bg-white px-3 py-1.5 text-sm text-slate-700 shadow ring-1 ring-slate-200">
          <MessageSquare size={14} aria-hidden /> Conversas
        </summary>
        <div className="mt-2 max-h-[60dvh] w-72 overflow-y-auto rounded-xl bg-white p-2 shadow-lg ring-1 ring-slate-200">{list}</div>
      </details>
    </>
  );
}
