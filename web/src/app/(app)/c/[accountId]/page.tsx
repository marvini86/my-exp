import { redirect } from "next/navigation";

export default async function AccountHome({ params }: { params: Promise<{ accountId: string }> }) {
  const { accountId } = await params;
  redirect(`/c/${accountId}/assistente`);
}
