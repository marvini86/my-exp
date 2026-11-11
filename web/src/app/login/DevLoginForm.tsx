"use client";

import { useActionState } from "react";

import { SubmitButton } from "@/components/SubmitButton";
import { Alert, Field, inputClass } from "@/components/ui";
import { devLogin, type LoginState } from "@/lib/auth/actions";

export function DevLoginForm({ next }: { next?: string }) {
  const [state, action] = useActionState<LoginState, FormData>(devLogin, {});

  return (
    <form action={action} className="space-y-4">
      <Alert tone="info">Modo desenvolvimento: entra só com o e-mail, sem senha.</Alert>
      <input type="hidden" name="next" value={next ?? "/"} />
      <Field label="E-mail" htmlFor="email">
        <input id="email" name="email" type="email" required autoComplete="email" className={inputClass} placeholder="voce@exemplo.com" />
      </Field>
      <Field label="Nome" htmlFor="name" hint="Usado só no primeiro acesso.">
        <input id="name" name="name" type="text" autoComplete="name" className={inputClass} placeholder="Opcional" maxLength={255} />
      </Field>
      {state.message && <Alert>{state.message}</Alert>}
      <SubmitButton pendingLabel="Entrando…" className="w-full">
        Entrar
      </SubmitButton>
    </form>
  );
}
