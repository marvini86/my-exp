import { api, unwrap } from "@/lib/api/server";
import { getAccount } from "@/lib/session";
import { ConversationList } from "./_components/ConversationList";

/** Assistant: conversation list (only your own) + chat filling the screen height. */
export default async function AssistantLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ accountId: string }>;
}) {
  const { accountId } = await params;
  const account = await getAccount(accountId);
  const conversations = await unwrap(
    (await api()).GET("/accounts/{account_id}/conversations/", { params: { path: { account_id: account.id } } }),
  );

  return (
    // Screen height minus the header (4rem) and the <main> padding (3rem).
    <div className="relative -mb-6 flex h-[calc(100dvh-7rem)] gap-6">
      <ConversationList
        accountId={account.id}
        conversations={conversations.map(({ id, title, updated_at }) => ({ id, title, updated_at }))}
      />
      <section className="min-w-0 flex-1">{children}</section>
    </div>
  );
}
