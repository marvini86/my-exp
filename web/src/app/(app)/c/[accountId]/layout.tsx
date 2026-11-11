import { Can } from "@/components/Can";
import { AccountSwitcher } from "@/components/shell/AccountSwitcher";
import { InviteMember } from "@/components/shell/InviteMember";
import { Sidebar } from "@/components/shell/Sidebar";
import { UserMenu } from "@/components/shell/UserMenu";
import { MANAGERS } from "@/lib/roles";
import { getAccount, getCurrentUser, getMyAccounts } from "@/lib/session";

export default async function AccountLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ accountId: string }>;
}) {
  const { accountId } = await params;
  const [user, accounts, account] = await Promise.all([getCurrentUser(), getMyAccounts(), getAccount(accountId)]);

  return (
    <div className="flex min-h-dvh">
      <Sidebar
        accountId={account.id}
        header={
          <>
            <AccountSwitcher
              currentId={account.id}
              accounts={accounts.map(({ id, name, role }) => ({ id, name, role }))}
            />
            {/* Invited users do not invite: only the platform admin and whoever they allowed. */}
            {user.can_invite && (
              <Can role={account.role} allow={MANAGERS}>
                <InviteMember accountId={account.id} />
              </Can>
            )}
          </>
        }
      />
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 items-center justify-end border-b border-slate-200 bg-white px-4 sm:px-8">
          <UserMenu user={user} />
        </header>
        <main className="flex-1 px-4 py-6 sm:px-8">{children}</main>
      </div>
    </div>
  );
}
