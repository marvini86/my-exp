import {
  Bot,
  FileText,
  LayoutDashboard,
  type LucideIcon,
} from "lucide-react";

export type NavItem = {
  segment: string;
  label: string;
  description: string;
  icon: LucideIcon;
  /** Full class names (Tailwind only generates classes written literally in the code). */
  tone: string;
};

export const NAV_ITEMS: NavItem[] = [
  { segment: "assistente", label: "Assistente", description: "Pergunte sobre seus gastos", icon: Bot, tone: "bg-violet-100 text-violet-700" },
  { segment: "documentos", label: "Documentos", description: "Notas, contas e recibos", icon: FileText, tone: "bg-sky-100 text-sky-700" },
  { segment: "painel", label: "Painel", description: "Resumo dos gastos", icon: LayoutDashboard, tone: "bg-emerald-100 text-emerald-700" },
];

/** Section opened when entering the account. */
export const HOME_SEGMENT = "assistente";

export function navItem(segment: string): NavItem {
  const item = NAV_ITEMS.find((i) => i.segment === segment);
  if (!item) throw new Error(`Item de navegação desconhecido: ${segment}`);
  return item;
}
