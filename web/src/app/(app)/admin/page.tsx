import type { Metadata } from "next";
import { ArrowLeft, ShieldCheck, Trash2, Users, Wallet } from "lucide-react";
import Link from "next/link";
import { notFound } from "next/navigation";

import { UserMenu } from "@/components/shell/UserMenu";
import { Card, EmptyState, PageHeader, buttonClass } from "@/components/ui";
import { api, unwrap } from "@/lib/api/server";
import { formatDateTime } from "@/lib/format";
import { getCurrentUser } from "@/lib/session";
import { GrantForm } from "./GrantForm";
import { revokeAccess } from "./actions";

export const metadata: Metadata = { title: "Administração" };

/** Who can sign in to the platform. Administrators only (AUTH_ADMIN_EMAILS in the API). */
export default async function AdminPage() {
  const user = await getCurrentUser();
  if (!user.is_platform_admin) notFound();

  const entries = await unwrap((await api()).GET("/admin/access"));

  return (
    <div className="min-h-dvh">
      <header className="flex h-16 items-center justify-between border-b border-slate-200 bg-white px-4 sm:px-8">
        <Link href="/" className="flex items-center gap-2 font-semibold text-slate-900">
          <Wallet size={22} className="text-emerald-700" aria-hidden />
          My Exp
        </Link>
        <UserMenu user={user} />
      </header>

      <main className="mx-auto max-w-3xl px-4 py-8">
        <Link href="/" className={`${buttonClass.ghost} -ml-3 mb-4`}>
          <ArrowLeft size={16} aria-hidden /> Voltar
        </Link>
        <PageHeader
          icon={ShieldCheck}
          tone="bg-slate-200 text-slate-700"
          title="Administração"
          description="Quem pode entrar na plataforma. Convidados das contas entram sem estar aqui."
        />

        <Card className="mb-6">
          <GrantForm />
        </Card>

        {entries.length === 0 ? (
          <EmptyState icon={Users} title="Nenhum e-mail liberado ainda" />
        ) : (
          <ul className="divide-y divide-slate-100 rounded-xl border border-slate-200 bg-white">
            {entries.map((entry) => (
              <li key={entry.id} className="flex items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate font-medium text-slate-800">{entry.name ?? entry.email}</p>
                  <p className="truncate text-sm text-slate-500">
                    {entry.name && <span>{entry.email} · </span>}
                    {entry.registered ? "já entrou" : "ainda não entrou"} · liberado em {formatDateTime(entry.created_at)}
                  </p>
                </div>
                <span
                  className={
                    entry.registered
                      ? "shrink-0 rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-800"
                      : "shrink-0 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800"
                  }
                >
                  {entry.registered ? "Ativo" : "Pendente"}
                </span>
                <form action={revokeAccess}>
                  <input type="hidden" name="id" value={entry.id} />
                  <button
                    type="submit"
                    className="rounded-lg p-2 text-slate-400 transition hover:bg-rose-50 hover:text-rose-700"
                    title={`Remover acesso de ${entry.email}`}
                    aria-label={`Remover acesso de ${entry.email}`}
                  >
                    <Trash2 size={16} aria-hidden />
                  </button>
                </form>
              </li>
            ))}
          </ul>
        )}

        <p className="mt-4 text-xs text-slate-500">
          Remover tira o acesso na próxima renovação de sessão (até 15 min), a menos que a pessoa seja membro de alguma conta.
        </p>
      </main>
    </div>
  );
}
