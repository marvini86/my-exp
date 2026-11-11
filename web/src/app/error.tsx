"use client";

import { AlertTriangle } from "lucide-react";

import { buttonClass } from "@/components/ui";

/**
 * Unexpected error on a page (e.g. API down). In production Next does not
 * forward the server message to the browser; we show a generic text.
 */
export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="flex min-h-[60dvh] flex-col items-center justify-center px-4 text-center">
      <AlertTriangle size={40} className="mb-3 text-amber-500" aria-hidden />
      <h1 className="text-xl font-semibold text-slate-900">Não foi possível carregar esta página</h1>
      <p className="mt-1 max-w-md text-sm text-slate-500">
        O servidor pode estar indisponível. Tente novamente em instantes.
      </p>
      <button type="button" onClick={reset} className={`${buttonClass.primary} mt-6`}>
        Tentar novamente
      </button>
    </main>
  );
}
