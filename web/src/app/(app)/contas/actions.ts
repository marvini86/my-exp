"use server";

import { redirect } from "next/navigation";

import { api, attempt } from "@/lib/api/server";
import { slugify } from "@/lib/slug";

export type CreateAccountState = { message?: string };

export async function createAccount(_: CreateAccountState, form: FormData): Promise<CreateAccountState> {
  const name = String(form.get("name") ?? "").trim();
  if (!name) return { message: "Informe o nome da conta." };
  if (name.length > 255) return { message: "Nome muito longo." };

  // The API validates the session: without a valid token the call returns 401 → login.
  const client = await api();
  let result = await attempt(client.POST("/accounts/", { body: { name, slug: slugify(name) } }));
  if (result.error?.status === 409) {
    // Random slug already exists (rare): try another suffix once.
    result = await attempt(client.POST("/accounts/", { body: { name, slug: slugify(name) } }));
  }
  if (result.error) return { message: result.error.message };

  redirect(`/c/${result.data.id}/assistente`);
}
