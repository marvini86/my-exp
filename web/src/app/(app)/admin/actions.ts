"use server";

import { revalidatePath } from "next/cache";

import { api, attempt } from "@/lib/api/server";
import { isUuid } from "@/lib/ids";

export type GrantState = { ok?: boolean; message?: string };

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/** The API refuses (404) if the caller is not a platform administrator. */
export async function grantAccess(_: GrantState, form: FormData): Promise<GrantState> {
  const email = String(form.get("email") ?? "").trim().toLowerCase();
  if (!EMAIL.test(email) || email.length > 255) return { ok: false, message: "Informe um e-mail válido." };

  const result = await attempt((await api()).POST("/admin/access", { body: { email } }));
  if (result.error) return { ok: false, message: result.error.message };

  revalidatePath("/admin");
  return { ok: true, message: `${email} liberado. Avise a pessoa para entrar com a conta Google desse e-mail.` };
}

export async function revokeAccess(form: FormData) {
  const id = form.get("id");
  if (!isUuid(id)) return;
  await attempt((await api()).DELETE("/admin/access/{access_id}", { params: { path: { access_id: id } } }));
  revalidatePath("/admin");
}
