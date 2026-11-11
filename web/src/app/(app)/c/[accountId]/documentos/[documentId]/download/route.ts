import { NextResponse, type NextRequest } from "next/server";

import { SESSION_EXPIRED_PATH, api } from "@/lib/api/server";
import { isUuid } from "@/lib/ids";

/**
 * Opens the original file: asks the API for a temporary URL (SAS, ~10 min,
 * read-only) and redirects. The blob URL is never stored in the page.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ accountId: string; documentId: string }> },
) {
  const { accountId, documentId } = await params;
  if (!isUuid(accountId) || !isUuid(documentId)) return new NextResponse("Não encontrado", { status: 404 });

  const { data, response } = await (await api()).GET("/accounts/{account_id}/documents/{document_id}/download", {
    params: { path: { account_id: accountId, document_id: documentId } },
  });

  if (response.status === 401) return NextResponse.redirect(new URL(SESSION_EXPIRED_PATH, request.url));
  if (!data) {
    const status = response.status === 404 ? 404 : 502;
    return new NextResponse(status === 404 ? "Documento não encontrado" : "Não foi possível abrir o arquivo agora", { status });
  }

  const target = new URL(data.url);
  // Only redirects to Azure storage (prevents an open redirect if something comes back wrong).
  if (target.protocol !== "https:" || !target.hostname.endsWith(".blob.core.windows.net")) {
    return new NextResponse("Destino de download inválido", { status: 502 });
  }

  const redirect = NextResponse.redirect(target);
  redirect.headers.set("Cache-Control", "no-store");
  return redirect;
}
