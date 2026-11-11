import type { Metadata } from "next";

import { getAccount } from "@/lib/session";
import { Chat } from "./_components/Chat";

export const metadata: Metadata = { title: "Assistente" };

/** New conversation (only created in the API when the 1st question is sent). */
export default async function NewConversationPage({
  params,
  searchParams,
}: {
  params: Promise<{ accountId: string }>;
  searchParams: Promise<{ pergunta?: string | string[] }>;
}) {
  const { accountId } = await params;
  const { pergunta } = await searchParams;
  const account = await getAccount(accountId);
  // Coming from the Dashboard: only fills the box; the person reviews and sends it (no AI spent on its own).
  const draft = typeof pergunta === "string" ? pergunta.slice(0, 500) : "";
  return <Chat key={`new-${draft}`} accountId={account.id} conversationId={null} initialMessages={[]} initialDraft={draft} />;
}
