import "server-only";

import { notFound } from "next/navigation";
import { cache } from "react";

import { api, unwrap } from "@/lib/api/server";
import type { components } from "@/lib/api/schema";

export type CurrentUser = components["schemas"]["MeResponse"];
export type MyAccount = components["schemas"]["MyAccountResponse"];

/** `cache`: the layout and the page of the same request share the call. */
export const getCurrentUser = cache(async (): Promise<CurrentUser> => {
  return unwrap((await api()).GET("/users/me"));
});

export const getMyAccounts = cache(async (): Promise<MyAccount[]> => {
  return unwrap((await api()).GET("/accounts/me"));
});

/** The URL's account with the user's role. Not a member → 404 (same as the API). */
export async function getAccount(accountId: string): Promise<MyAccount> {
  const account = (await getMyAccounts()).find((a) => a.id === accountId);
  if (!account) notFound();
  return account;
}
