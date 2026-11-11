"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { api, apiUrl } from "@/lib/api/server";
import { clearedSessionCookies, sessionCookies, type TokenPair } from "@/lib/auth/cookies";
import { apiErrorMessage } from "@/lib/errors";

export type LoginState = { message?: string };

/** Only accepts internal paths: avoids redirecting to another site (open redirect). */
function safeNext(value: FormDataEntryValue | null): string {
  const next = typeof value === "string" ? value : "";
  return next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/\\") ? next : "/";
}

async function storeSession(tokens: TokenPair) {
  const store = await cookies();
  sessionCookies(tokens).forEach(({ name, value, options }) => store.set(name, value, options));
}

export async function devLogin(_: LoginState, form: FormData): Promise<LoginState> {
  if (process.env.DEV_LOGIN_ENABLED !== "true") return { message: "Login de desenvolvimento desativado." };

  const email = String(form.get("email") ?? "").trim();
  const name = String(form.get("name") ?? "").trim();
  if (!email) return { message: "Informe o e-mail." };

  let response: Response;
  try {
    response = await fetch(`${apiUrl()}/auth/dev-login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, name: name || null }),
      cache: "no-store",
    });
  } catch {
    return { message: "API indisponível. Verifique se ela está rodando." };
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    return { message: response.status === 404 ? "Login de desenvolvimento indisponível na API." : apiErrorMessage(body, response.status) };
  }

  await storeSession(body as TokenPair);
  redirect(safeNext(form.get("next")));
}

/** Exchanges the Google Identity Services ID token for an API session. */
export async function googleLogin(credential: string, next: string | null): Promise<LoginState> {
  if (!credential) return { message: "O Google não devolveu a credencial. Tente de novo." };

  let response: Response;
  try {
    response = await fetch(`${apiUrl()}/auth/google`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id_token: credential }),
      cache: "no-store",
    });
  } catch {
    return { message: "API indisponível. Tente de novo em instantes." };
  }

  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) return { message: apiErrorMessage(body, response.status) };

  await storeSession(body as TokenPair);
  redirect(safeNext(next));
}

export async function logout(form: FormData) {
  const everywhere = form.get("everywhere") === "true";
  // Best effort: even if the API fails, the local session is ended.
  await (await api()).POST("/auth/logout", { params: { query: { everywhere } } }).catch(() => undefined);

  const store = await cookies();
  clearedSessionCookies().forEach(({ name, value, options }) => store.set(name, value, options));
  redirect("/login");
}
