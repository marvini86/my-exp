"use client";

import { usePathname, useRouter } from "next/navigation";

import { ROLE_LABELS, type AccountRole } from "@/lib/roles";

type AccountOption = { id: string; name: string; role: AccountRole };

/** Switches accounts keeping the current section (e.g. Documents → the other account's Documents). */
export function AccountSwitcher({ accounts, currentId }: { accounts: AccountOption[]; currentId: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const current = accounts.find((a) => a.id === currentId);

  function switchTo(id: string) {
    if (id === "__all") return router.push("/contas");
    const section = pathname.split("/")[3] ?? "assistente";
    router.push(`/c/${id}/${section}`);
  }

  return (
    <div>
      <label htmlFor="account-switcher" className="mb-1 block text-xs uppercase tracking-wide text-slate-400">
        Conta
      </label>
      <select
        id="account-switcher"
        value={currentId}
        onChange={(event) => switchTo(event.target.value)}
        className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-white focus:border-emerald-500 focus:outline-none"
      >
        {accounts.map((account) => (
          <option key={account.id} value={account.id}>
            {account.name}
          </option>
        ))}
        <option value="__all">Ver todas / criar conta…</option>
      </select>
      {current && <p className="mt-1 text-xs text-slate-400">Seu papel: {ROLE_LABELS[current.role]}</p>}
    </div>
  );
}
