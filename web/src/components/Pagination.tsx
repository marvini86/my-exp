import { ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";

import { buttonClass } from "@/components/ui";

/** Link-based pagination (state in the URL). `params` = other filters to keep. */
export function Pagination({
  basePath,
  page,
  pageSize,
  total,
  params = {},
}: {
  basePath: string;
  page: number;
  pageSize: number;
  total: number;
  params?: Record<string, string | undefined>;
}) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;

  const href = (target: number) => {
    const query = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => value && query.set(key, value));
    if (target > 1) query.set("pagina", String(target));
    const qs = query.toString();
    return qs ? `${basePath}?${qs}` : basePath;
  };

  const disabled = "pointer-events-none opacity-40";

  return (
    <nav className="mt-4 flex items-center justify-between text-sm text-slate-600" aria-label="Paginação">
      <span>
        Página {page} de {pages} · {total} {total === 1 ? "item" : "itens"}
      </span>
      <div className="flex gap-2">
        <Link href={href(page - 1)} className={`${buttonClass.secondary} ${page <= 1 ? disabled : ""}`} aria-disabled={page <= 1}>
          <ChevronLeft size={16} aria-hidden /> Anterior
        </Link>
        <Link href={href(page + 1)} className={`${buttonClass.secondary} ${page >= pages ? disabled : ""}`} aria-disabled={page >= pages}>
          Próxima <ChevronRight size={16} aria-hidden />
        </Link>
      </div>
    </nav>
  );
}

/** "?pagina=3" → 3 (invalid → 1). */
export function pageFromParam(value: string | string[] | undefined): number {
  const n = Number(Array.isArray(value) ? value[0] : value);
  return Number.isInteger(n) && n > 0 ? n : 1;
}
