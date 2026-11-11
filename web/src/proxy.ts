import { NextResponse, type NextRequest } from "next/server";

import {
  ACCESS_COOKIE,
  REFRESH_COOKIE,
  clearedSessionCookies,
  lastAccountCookie,
  sessionCookies,
  type CookieSpec,
} from "@/lib/auth/cookies";
import { refreshSession } from "@/lib/auth/refresh";

/**
 * Before each page:
 * - no session → /login;
 * - expired access token (cookie expired) → refreshes with the refresh token and passes the
 *   new cookies both to the browser and to the current render;
 * - remembers the last opened account (so "/" opens it directly).
 *
 * Does not replace the API check: every call carries the token and FastAPI decides.
 */

const PUBLIC_PATHS = ["/login", "/session/expired"];
const ACCOUNT_PATH = /^\/c\/([0-9a-f-]{36})(?:\/|$)/;

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some((path) => pathname === path || pathname.startsWith(`${path}/`));
}

function redirectToLogin(request: NextRequest, cookies: CookieSpec[] = []) {
  const url = new URL("/login", request.url);
  const next = `${request.nextUrl.pathname}${request.nextUrl.search}`;
  if (next !== "/") url.searchParams.set("next", next);
  const response = NextResponse.redirect(url);
  cookies.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
  return response;
}

export async function proxy(request: NextRequest) {
  const { pathname } = request.nextUrl;
  if (isPublic(pathname)) return NextResponse.next();

  const access = request.cookies.get(ACCESS_COOKIE)?.value;
  const refresh = request.cookies.get(REFRESH_COOKIE)?.value;

  if (!access && !refresh) return redirectToLogin(request);

  const issued: CookieSpec[] = [];

  if (!access && refresh) {
    const result = await refreshSession(process.env.API_URL ?? "", refresh);

    if (result.kind === "invalid") return redirectToLogin(request, clearedSessionCookies());
    if (result.kind === "rotated") {
      // Another parallel request already refreshed: repeat the navigation with the new cookies.
      return NextResponse.redirect(request.url);
    }
    if (result.kind === "ok") {
      issued.push(...sessionCookies(result.tokens));
      // This request's render already uses the new token.
      issued.forEach(({ name, value }) => request.cookies.set(name, value));
    }
    // "unavailable": carry on; the page shows the API error.
  }

  const accountId = ACCOUNT_PATH.exec(pathname)?.[1];
  if (accountId && request.cookies.get(lastAccountCookie(accountId).name)?.value !== accountId) {
    issued.push(lastAccountCookie(accountId));
  }

  const response = NextResponse.next({ request: { headers: request.headers } });
  issued.forEach(({ name, value, options }) => response.cookies.set(name, value, options));
  return response;
}

export const config = {
  // Everything except static files and images.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|webp|ico)$).*)"],
};
