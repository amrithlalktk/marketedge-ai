"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { InlineError } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

function LoginForm() {
  const { login } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totp, setTotp] = useState("");
  const [needTotp, setNeedTotp] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const registered = params.get("registered") === "1";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email.trim(), password, needTotp ? totp.trim() : undefined);
      const next = params.get("next");
      router.replace(next && next.startsWith("/") && !next.startsWith("//") ? next : "/");
    } catch (err) {
      if (err instanceof ApiError && err.status === 401 && err.headers?.get("X-2FA-Required") === "1") {
        setNeedTotp(true);
        setError(null);
      } else {
        setError(err instanceof Error ? err.message : "Sign-in failed");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h1 className="text-lg font-semibold">Sign in to MarketEdge AI</h1>
      <p className="mt-1 text-sm text-muted">Swing-trading analysis — historical statistics, not guarantees.</p>
      {registered && <p className="mt-3 rounded border border-green-900 bg-green-950/40 px-2 py-1.5 text-sm text-green-200">Account created. Please sign in.</p>}
      <form className="mt-4 space-y-3" onSubmit={submit} noValidate>
        <div>
          <label className="label" htmlFor="email">Email</label>
          <input id="email" className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="password">Password</label>
          <input id="password" className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        {needTotp && (
          <div>
            <label className="label" htmlFor="totp">Two-factor code</label>
            <input id="totp" className="input num tracking-widest" inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]*" maxLength={10} autoFocus required value={totp} onChange={(e) => setTotp(e.target.value)} />
            <p className="mt-1 text-[11px] text-muted">Enter the 6-digit code from your authenticator app.</p>
          </div>
        )}
        <InlineError error={error} />
        <button className="btn-primary w-full" disabled={busy || !email || !password || (needTotp && totp.length < 6)}>
          {busy ? "Signing in…" : needTotp ? "Verify & sign in" : "Sign in"}
        </button>
      </form>
      <SignupLink />
    </div>
  );
}

/** Shown only while sign-up is open (ALLOW_REGISTRATION); single-user installs close it. */
function SignupLink() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    api.authConfig().then((c) => setOpen(c.registration_open)).catch(() => setOpen(false));
  }, []);
  if (!open) return null;
  return (
    <p className="mt-4 text-sm text-muted">
      No account? <Link className="link" href="/register">Create one</Link>
    </p>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
