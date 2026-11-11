"use client";

import { Loader2 } from "lucide-react";
import { useFormStatus } from "react-dom";

import { buttonClass } from "@/components/ui";

/** Submit button that disables itself while the Server Action runs. */
export function SubmitButton({
  children,
  pendingLabel = "Salvando…",
  variant = "primary",
  className,
}: {
  children: React.ReactNode;
  pendingLabel?: string;
  variant?: keyof typeof buttonClass;
  className?: string;
}) {
  const { pending } = useFormStatus();
  return (
    <button type="submit" disabled={pending} className={`${buttonClass[variant]} ${className ?? ""}`}>
      {pending && <Loader2 size={16} className="animate-spin" aria-hidden />}
      {pending ? pendingLabel : children}
    </button>
  );
}
