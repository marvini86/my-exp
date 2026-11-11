"use server";

import { api, attempt } from "@/lib/api/server";
import { isUuid } from "@/lib/ids";

export type InviteState = { ok?: boolean; message?: string };

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * Invites someone by e-mail (role "member"). If the person does not have an
 * account yet, the API creates a pending invitation, linked on the first login.
 */
export async function inviteMember(accountId: string, _: InviteState, form: FormData): Promise<InviteState> {
  const email = String(form.get("email") ?? "").trim().toLowerCase();
  if (!isUuid(accountId)) return { ok: false, message: "Conta inválida." };
  if (!EMAIL.test(email) || email.length > 255) return { ok: false, message: "Informe um e-mail válido." };

  const result = await attempt(
    (await api()).POST("/accounts/{account_id}/members/", {
      params: { path: { account_id: accountId } },
      body: { email, role: "member" },
    }),
  );
  if (result.error) return { ok: false, message: result.error.message };

  return {
    ok: true,
    message: result.data.pending
      ? `Convite criado. ${email} entra na conta no primeiro acesso.`
      : `${result.data.name ?? email} agora faz parte da conta.`,
  };
}
