import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { api, unwrap } from "@/lib/api/server";
import { isUuid } from "@/lib/ids";
import { getAccount } from "@/lib/session";
import { Chat } from "../_components/Chat";

export const metadata: Metadata = { title: "Assistente" };

export default async function ConversationPage({
  params,
}: {
  params: Promise<{ accountId: string; conversationId: string }>;
}) {
  const { accountId, conversationId } = await params;
  if (!isUuid(conversationId)) notFound();
  const account = await getAccount(accountId);

  // Conversations are private: another member's conversation returns 404 from the API.
  const messages = await unwrap(
    (await api()).GET("/accounts/{account_id}/conversations/{conversation_id}/messages", {
      params: { path: { account_id: account.id, conversation_id: conversationId } },
    }),
  );

  return <Chat key={conversationId} accountId={account.id} conversationId={conversationId} initialMessages={messages} />;
}
