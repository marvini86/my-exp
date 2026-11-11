"use client";

import { UserPlus } from "lucide-react";
import { useActionState } from "react";

import { SubmitButton } from "@/components/SubmitButton";
import { Alert, inputClass } from "@/components/ui";
import { grantAccess, type GrantState } from "./actions";

export function GrantForm() {
  const [state, action] = useActionState<GrantState, FormData>(grantAccess, {});

  return (
    <form action={action} className="space-y-3">
      <label htmlFor="grant-email" className="block text-sm font-medium text-slate-700">
        Liberar acesso para
      </label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input id="grant-email" name="email" type="email" required maxLength={255} placeholder="email@gmail.com" className={inputClass} />
        <SubmitButton pendingLabel="Liberando…">
          <UserPlus size={16} aria-hidden /> Liberar
        </SubmitButton>
      </div>
      <p className="text-xs text-slate-500">
        A pessoa entra com a conta Google desse e-mail, cria a própria conta e pode convidar a família.
        Quem ela convidar usa a conta, mas não convida outras pessoas.
      </p>
      {state.message && <Alert tone={state.ok ? "success" : "error"}>{state.message}</Alert>}
    </form>
  );
}
