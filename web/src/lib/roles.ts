import type { components } from "@/lib/api/schema";

export type AccountRole = components["schemas"]["AccountRole"];

export const ROLE_LABELS: Record<AccountRole, string> = {
  owner: "Dono",
  admin: "Administrador",
  member: "Membro",
};

/** Mirrors the API rules (services/account_access.py); the API remains the authority. */
export const MANAGERS: readonly AccountRole[] = ["owner", "admin"];

export function hasRole(role: AccountRole, allowed: readonly AccountRole[]): boolean {
  return allowed.includes(role);
}
