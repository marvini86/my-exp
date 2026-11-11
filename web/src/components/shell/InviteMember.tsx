"use client";

import { UserPlus } from "lucide-react";
import { useActionState } from "react";

import { SubmitButton } from "@/components/SubmitButton";
import { inviteMember, type InviteState } from "@/app/(app)/c/[accountId]/actions";

/** Invitation by e-mail, tucked into the account menu (no members screen). */
export function InviteMember({ accountId }: { accountId: string }) {
  const [state, action] = useActionState<InviteState, FormData>(inviteMember.bind(null, accountId), {});

  return (
    <details className="mt-3 text-sm">
      <summary className="flex cursor-pointer list-none items-center gap-2 text-slate-300 hover:text-white">
        <UserPlus size={16} aria-hidden /> Convidar alguém
      </summary>
      <form action={action} className="mt-2 space-y-2">
        <label htmlFor="invite-email" className="sr-only">E-mail</label>
        <input
          id="invite-email"
          name="email"
          type="email"
          required
          maxLength={255}
          placeholder="email@exemplo.com"
          className="w-full rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-white placeholder:text-slate-500 focus:border-emerald-500 focus:outline-none"
        />
        <SubmitButton pendingLabel="Enviando…" className="w-full !py-1.5 text-sm">
          Convidar
        </SubmitButton>
        {state.message && (
          <p role="status" className={state.ok ? "text-emerald-300" : "text-rose-300"}>
            {state.message}
          </p>
        )}
      </form>
    </details>
  );
}
