import { LogOut, ShieldCheck } from "lucide-react";
import Link from "next/link";

import { SubmitButton } from "@/components/SubmitButton";
import { logout } from "@/lib/auth/actions";
import type { CurrentUser } from "@/lib/session";

export function UserMenu({ user }: { user: CurrentUser }) {
  const initial = (user.name ?? user.email ?? "?").trim().charAt(0).toUpperCase();

  return (
    <div className="flex items-center gap-3">
      <div className="hidden text-right sm:block">
        <p className="text-sm font-medium text-slate-800">{user.name ?? "Sem nome"}</p>
        <p className="text-xs text-slate-500">{user.email}</p>
      </div>
      <span className="flex h-9 w-9 items-center justify-center rounded-full bg-emerald-100 font-semibold text-emerald-800" aria-hidden>
        {initial}
      </span>
      {user.is_platform_admin && (
        <Link href="/admin" className="rounded-lg p-2 text-slate-600 transition hover:bg-slate-100 hover:text-slate-900" title="Administração" aria-label="Administração">
          <ShieldCheck size={18} aria-hidden />
        </Link>
      )}
      <form action={logout}>
        <SubmitButton variant="ghost" pendingLabel="Saindo…">
          <LogOut size={16} aria-hidden />
          <span className="hidden sm:inline">Sair</span>
        </SubmitButton>
      </form>
    </div>
  );
}
