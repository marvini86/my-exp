"use client";

import clsx from "clsx";
import { Menu, Wallet, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";

import { NAV_ITEMS } from "./nav";

/** Side menu: fixed on desktop, drawer on mobile. */
export function Sidebar({ accountId, header }: { accountId: string; header: React.ReactNode }) {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  const base = `/c/${accountId}`;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="fixed left-3 top-3 z-30 rounded-lg bg-white p-2 shadow md:hidden"
        aria-label="Abrir menu"
      >
        <Menu size={22} />
      </button>

      {open && <div className="fixed inset-0 z-40 bg-black/40 md:hidden" onClick={() => setOpen(false)} aria-hidden />}

      <aside
        className={clsx(
          "fixed inset-y-0 left-0 z-50 flex w-72 flex-col bg-slate-900 text-white shadow-xl transition-transform duration-200 md:sticky md:top-0 md:h-dvh md:translate-x-0",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex h-16 items-center justify-between border-b border-slate-800 px-4">
          <Link href={`${base}/assistente`} onClick={() => setOpen(false)} className="flex items-center gap-2 font-semibold">
            <Wallet size={22} className="text-emerald-400" aria-hidden />
            My Exp
          </Link>
          <button type="button" onClick={() => setOpen(false)} className="rounded p-1 md:hidden" aria-label="Fechar menu">
            <X size={20} />
          </button>
        </div>

        <div className="border-b border-slate-800 p-3">{header}</div>

        <nav className="flex-1 space-y-1 overflow-y-auto p-3" aria-label="Principal">
          {NAV_ITEMS.map(({ segment, label, icon: Icon }) => {
            const href = `${base}/${segment}`;
            const active = pathname === href || pathname.startsWith(`${href}/`);
            return (
              <Link
                key={segment}
                href={href}
                aria-current={active ? "page" : undefined}
                onClick={() => setOpen(false)}
                className={clsx(
                  "flex items-center gap-3 rounded-lg px-3 py-2.5 transition",
                  active ? "bg-emerald-700 text-white shadow" : "text-slate-300 hover:bg-slate-800 hover:text-white",
                )}
              >
                <Icon size={18} aria-hidden />
                {label}
              </Link>
            );
          })}
        </nav>
      </aside>
    </>
  );
}
