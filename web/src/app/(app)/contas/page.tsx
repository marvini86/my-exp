import type { Metadata } from "next";
import { ChevronRight, Wallet } from "lucide-react";
import Link from "next/link";

import { UserMenu } from "@/components/shell/UserMenu";
import { Card, EmptyState, PageHeader } from "@/components/ui";
import { ROLE_LABELS } from "@/lib/roles";
import { getCurrentUser, getMyAccounts } from "@/lib/session";
import { CreateAccountForm } from "./CreateAccountForm";

export const metadata: Metadata = { title: "Contas" };

export default async function AccountsPage() {
  const [user, accounts] = await Promise.all([getCurrentUser(), getMyAccounts()]);

  return (
    <div className="min-h-dvh">
      <header className="flex h-16 items-center justify-between border-b border-slate-200 bg-white px-4 sm:px-8">
        <span className="flex items-center gap-2 font-semibold text-slate-900">
          <Wallet size={22} className="text-emerald-700" aria-hidden />
          My Exp
        </span>
        <UserMenu user={user} />
      </header>

      <main className="mx-auto max-w-3xl px-4 py-8">
        <PageHeader
          icon={Wallet}
          tone="bg-emerald-100 text-emerald-700"
          title="Suas contas"
          description="Cada conta tem seus próprios documentos, gastos e membros."
        />

        {accounts.length ? (
          <ul className="mb-6 space-y-2">
            {accounts.map((account) => (
              <li key={account.id}>
                <Link
                  href={`/c/${account.id}/assistente`}
                  className="flex items-center justify-between rounded-xl border border-slate-200 bg-white px-5 py-4 shadow-sm transition hover:border-emerald-300 hover:shadow"
                >
                  <span>
                    <span className="block font-medium text-slate-900">{account.name}</span>
                    <span className="text-sm text-slate-500">{ROLE_LABELS[account.role]}</span>
                  </span>
                  <ChevronRight size={18} className="text-slate-400" aria-hidden />
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <div className="mb-6">
            <EmptyState icon={Wallet} title="Você ainda não participa de nenhuma conta">
              Crie a sua abaixo ou peça para alguém da família te convidar pelo seu e-mail.
            </EmptyState>
          </div>
        )}

        <Card>
          <CreateAccountForm />
        </Card>
      </main>
    </div>
  );
}
