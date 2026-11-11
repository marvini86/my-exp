import type { Metadata } from "next";
import { Wallet } from "lucide-react";

import { Alert, Card } from "@/components/ui";
import { DevLoginForm } from "./DevLoginForm";
import { GoogleLoginButton } from "./GoogleLoginButton";

export const metadata: Metadata = { title: "Entrar" };

export default async function LoginPage({ searchParams }: { searchParams: Promise<{ next?: string }> }) {
  const { next } = await searchParams;
  const devLogin = process.env.DEV_LOGIN_ENABLED === "true";
  // The Client ID is not a secret (it goes to the browser); the OAuth secret is not used.
  const googleClientId = process.env.GOOGLE_CLIENT_ID?.trim();

  return (
    <main className="flex min-h-dvh items-center justify-center bg-gradient-to-br from-emerald-50 to-slate-100 px-4">
      <Card className="w-full max-w-sm p-8">
        <div className="mb-6 flex flex-col items-center text-center">
          <div className="mb-3 rounded-2xl bg-emerald-700 p-3 text-white">
            <Wallet size={28} aria-hidden />
          </div>
          <h1 className="text-2xl font-bold text-slate-900">My Exp</h1>
          <p className="text-sm text-slate-500">Despesas da família, organizadas.</p>
        </div>

        {googleClientId ? (
          <GoogleLoginButton clientId={googleClientId} next={next} />
        ) : (
          !devLogin && <Alert tone="info">Login com Google ainda não configurado.</Alert>
        )}

        {devLogin && (
          <div className={googleClientId ? "mt-6 border-t border-slate-200 pt-6" : undefined}>
            <DevLoginForm next={next} />
          </div>
        )}
      </Card>
    </main>
  );
}
