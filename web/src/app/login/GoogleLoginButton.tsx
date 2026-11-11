"use client";

import Script from "next/script";
import { useCallback, useEffect, useRef, useState, useTransition } from "react";

import { Alert } from "@/components/ui";
import { googleLogin } from "@/lib/auth/actions";

// Minimal typing of Google Identity Services (https://developers.google.com/identity/gsi/web/reference/js-reference).
type CredentialResponse = { credential?: string };
type GoogleAccounts = {
  id: {
    initialize(config: { client_id: string; callback: (response: CredentialResponse) => void; ux_mode?: "popup" }): void;
    renderButton(parent: HTMLElement, options: Record<string, string | number>): void;
  };
};
declare global {
  interface Window {
    google?: { accounts: GoogleAccounts };
  }
}

export function GoogleLoginButton({ clientId, next }: { clientId: string; next?: string }) {
  const container = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  const [message, setMessage] = useState<string>();
  const [pending, startTransition] = useTransition();

  const onCredential = useCallback(
    (response: CredentialResponse) => {
      setMessage(undefined);
      startTransition(async () => {
        // On success the action redirects; it only comes back here on error.
        const result = await googleLogin(response.credential ?? "", next ?? null);
        if (result?.message) setMessage(result.message);
      });
    },
    [next],
  );

  useEffect(() => {
    const google = window.google;
    if (!ready || !google || !container.current) return;
    google.accounts.id.initialize({ client_id: clientId, callback: onCredential, ux_mode: "popup" });
    google.accounts.id.renderButton(container.current, {
      type: "standard",
      theme: "outline",
      size: "large",
      text: "signin_with",
      shape: "pill",
      locale: "pt-BR",
      width: 280,
    });
  }, [ready, clientId, onCredential]);

  return (
    <div className="space-y-4">
      <Script src="https://accounts.google.com/gsi/client" strategy="afterInteractive" onReady={() => setReady(true)} />
      <div className="flex min-h-11 justify-center" aria-busy={pending}>
        <div ref={container} className={pending ? "pointer-events-none opacity-50" : undefined} />
      </div>
      {pending && <p className="text-center text-sm text-slate-500">Entrando…</p>}
      {message && <Alert>{message}</Alert>}
    </div>
  );
}
