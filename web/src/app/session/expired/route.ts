import { NextResponse, type NextRequest } from "next/server";

import { clearedSessionCookies } from "@/lib/auth/cookies";

/** Session refused by the API (revoked/expired): clears the cookies and goes back to login. */
export function GET(request: NextRequest) {
  const response = NextResponse.redirect(new URL("/login", request.url));
  clearedSessionCookies().forEach(({ name, value, options }) => response.cookies.set(name, value, options));
  return response;
}
