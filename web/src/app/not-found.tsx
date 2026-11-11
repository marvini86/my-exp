import { SearchX } from "lucide-react";
import Link from "next/link";

import { buttonClass } from "@/components/ui";

export default function NotFound() {
  return (
    <main className="flex min-h-[60dvh] flex-col items-center justify-center px-4 text-center">
      <SearchX size={40} className="mb-3 text-slate-400" aria-hidden />
      <h1 className="text-xl font-semibold text-slate-900">Não encontrado</h1>
      <p className="mt-1 text-sm text-slate-500">A página não existe ou você não tem acesso a ela.</p>
      <Link href="/contas" className={`${buttonClass.secondary} mt-6`}>
        Ir para minhas contas
      </Link>
    </main>
  );
}
