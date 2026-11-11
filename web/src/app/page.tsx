import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { LAST_ACCOUNT_COOKIE } from "@/lib/auth/cookies";

/** "/" opens the last account used; without history, the account list. */
export default async function Home() {
  const last = (await cookies()).get(LAST_ACCOUNT_COOKIE)?.value;
  redirect(last ? `/c/${last}/assistente` : "/contas");
}
