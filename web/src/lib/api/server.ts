import "server-only";

import createClient from "openapi-fetch";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { ACCESS_COOKIE } from "@/lib/auth/cookies";
import { ApiError } from "@/lib/errors";
import type { paths } from "./schema";

/** Route that clears the cookies and sends to login (Server Components cannot delete cookies). */
export const SESSION_EXPIRED_PATH = "/session/expired";

export function apiUrl(): string {
  const url = process.env.API_URL;
  if (!url) throw new Error("API_URL não configurada (.env)");
  return url.replace(/\/+$/, "");
}

/**
 * Typed FastAPI client, with the access token from the cookie.
 * Server only: the token never reaches the browser.
 */
export async function api() {
  const token = (await cookies()).get(ACCESS_COOKIE)?.value;
  return createClient<paths>({
    baseUrl: apiUrl(),
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    cache: "no-store",
  });
}

type FetchResult<T> = { data?: T; error?: unknown; response: Response };

/**
 * For Server Components: returns the data or handles the error the standard way
 * (401 → login, 404 → 404 page, others → ApiError for error.tsx).
 */
export async function unwrap<T>(request: Promise<FetchResult<T>>): Promise<T> {
  const { data, error, response } = await request;
  if (response.ok) return data as T;
  if (response.status === 401) redirect(SESSION_EXPIRED_PATH);
  if (response.status === 404) notFound();
  throw new ApiError(response.status, error, retryAfter(response));
}

function retryAfter(response: Response): number | undefined {
  const seconds = Number(response.headers.get("retry-after"));
  return Number.isFinite(seconds) && seconds > 0 ? seconds : undefined;
}

export type ActionState = { ok: boolean; message?: string };

/**
 * For Server Actions: turns an error into a message for the form.
 * 401 also redirects: the session ended while in use.
 */
export async function attempt<T>(
  request: Promise<FetchResult<T>>,
): Promise<{ data: T; error?: undefined } | { data?: undefined; error: ApiError }> {
  const { data, error, response } = await request;
  if (response.ok) return { data: data as T };
  if (response.status === 401) redirect(SESSION_EXPIRED_PATH);
  return { error: new ApiError(response.status, error, retryAfter(response)) };
}
