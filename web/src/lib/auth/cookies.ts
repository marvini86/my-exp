/**
 * Session cookies. All httpOnly: the browser's JavaScript never sees the tokens.
 * Used by the proxy, Server Actions and Route Handlers (all on the server).
 */

export const ACCESS_COOKIE = "mx_at";
export const REFRESH_COOKIE = "mx_rt";
export const LAST_ACCOUNT_COOKIE = "mx_account";

/** The access token cookie expires before the token: the proxy refreshes with slack. */
export const ACCESS_EXPIRY_MARGIN_SECONDS = 60;

export type TokenPair = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
  refresh_expires_in: number;
};

export type CookieOptions = {
  httpOnly: true;
  secure: boolean;
  sameSite: "lax";
  path: "/";
  maxAge: number;
};

export type CookieSpec = { name: string; value: string; options: CookieOptions };

function cookieOptions(maxAge: number): CookieOptions {
  return {
    httpOnly: true,
    // In dev (http://localhost) the browser would reject "secure" cookies.
    secure: process.env.NODE_ENV === "production",
    sameSite: "lax",
    path: "/",
    maxAge,
  };
}

export function sessionCookies(tokens: TokenPair): CookieSpec[] {
  return [
    {
      name: ACCESS_COOKIE,
      value: tokens.access_token,
      options: cookieOptions(Math.max(tokens.expires_in - ACCESS_EXPIRY_MARGIN_SECONDS, 1)),
    },
    { name: REFRESH_COOKIE, value: tokens.refresh_token, options: cookieOptions(tokens.refresh_expires_in) },
  ];
}

export function clearedSessionCookies(): CookieSpec[] {
  return [ACCESS_COOKIE, REFRESH_COOKIE].map((name) => ({ name, value: "", options: cookieOptions(0) }));
}

export function lastAccountCookie(accountId: string): CookieSpec {
  return { name: LAST_ACCOUNT_COOKIE, value: accountId, options: cookieOptions(60 * 60 * 24 * 365) };
}
