import type { TokenPair } from "./cookies";

export type RefreshResult =
  | { kind: "ok"; tokens: TokenPair }
  /** 409: another request has just refreshed; the new cookie is on its way. */
  | { kind: "rotated" }
  /** 401/422: invalid, expired or revoked refresh token → new login. */
  | { kind: "invalid" }
  /** API down: does not drop the session. */
  | { kind: "unavailable" };

export async function refreshSession(
  apiUrl: string,
  refreshToken: string,
  fetchImpl: typeof fetch = fetch,
): Promise<RefreshResult> {
  let response: Response;
  try {
    response = await fetchImpl(`${apiUrl}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    });
  } catch {
    return { kind: "unavailable" };
  }

  if (response.ok) return { kind: "ok", tokens: (await response.json()) as TokenPair };
  if (response.status === 409) return { kind: "rotated" };
  if (response.status === 401 || response.status === 422) return { kind: "invalid" };
  return { kind: "unavailable" };
}
