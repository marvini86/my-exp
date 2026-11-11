"use client";

import { Plus } from "lucide-react";
import { useActionState } from "react";

import { SubmitButton } from "@/components/SubmitButton";
import { Alert, inputClass } from "@/components/ui";
import { createAccount, type CreateAccountState } from "./actions";

export function CreateAccountForm() {
  const [state, action] = useActionState<CreateAccountState, FormData>(createAccount, {});

  return (
    <form action={action} className="space-y-3">
      <label htmlFor="account-name" className="block text-sm font-medium text-slate-700">
        Nova conta
      </label>
      <div className="flex flex-col gap-2 sm:flex-row">
        <input
          id="account-name"
          name="name"
          required
          maxLength={255}
          placeholder="Ex.: Família Souza, Pessoal"
          className={inputClass}
        />
        <SubmitButton pendingLabel="Criando…">
          <Plus size={16} aria-hidden />
          Criar
        </SubmitButton>
      </div>
      {state.message && <Alert>{state.message}</Alert>}
    </form>
  );
}
