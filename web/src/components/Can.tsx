import { hasRole, type AccountRole } from "@/lib/roles";

/**
 * Shows the content only to the allowed roles. UI only:
 * the API refuses (403) even if someone calls the action directly.
 */
export function Can({
  role,
  allow,
  fallback = null,
  children,
}: {
  role: AccountRole;
  allow: readonly AccountRole[];
  fallback?: React.ReactNode;
  children: React.ReactNode;
}) {
  return <>{hasRole(role, allow) ? children : fallback}</>;
}
