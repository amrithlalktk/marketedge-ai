"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { InlineError } from "@/components/ui";
import { api } from "@/lib/api";
import { passwordProblem } from "@/lib/constants";

export default function RegisterPage() {
  const [closed, setClosed] = useState(false);
  useEffect(() => {
    api.authConfig().then((c) => setClosed(!c.registration_open)).catch(() => undefined);
  }, []);
  if (closed)
    return (
      <div role="status" className="rounded-md border border-edge bg-panel p-4 text-sm">
        <p className="font-semibold">Sign-up is closed</p>
        <p className="mt-1 text-muted">This installation is private. <Link className="link" href="/login">Sign in</Link></p>
      </div>
    );
  return <RegisterForm />;
}

function RegisterForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const problem = password ? passwordProblem(password) : null;
  const mismatch = confirm.length > 0 && confirm !== password;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const p = passwordProblem(password);
    if (p) return setError(p);
    if (password !== confirm) return setError("Passwords do not match.");
    setBusy(true);
    setError(null);
    try {
      await api.auth.register(email.trim(), password, name.trim());
      router.replace("/login?registered=1");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h1 className="text-lg font-semibold">Create your account</h1>
      <p className="mt-1 text-sm text-muted">New accounts start on the Standard plan.</p>
      <form className="mt-4 space-y-3" onSubmit={submit} noValidate>
        <div>
          <label className="label" htmlFor="name">Full name</label>
          <input id="name" className="input" autoComplete="name" maxLength={255} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="email">Email</label>
          <input id="email" className="input" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div>
          <label className="label" htmlFor="password">Password</label>
          <input id="password" className="input" type="password" autoComplete="new-password" required minLength={10} maxLength={128} aria-describedby="pw-rules" aria-invalid={!!problem} value={password} onChange={(e) => setPassword(e.target.value)} />
          <p id="pw-rules" className={problem ? "mt-1 text-[11px] text-amber-300" : "mt-1 text-[11px] text-muted"}>
            {problem ?? "At least 10 characters, using 3 of: lowercase, uppercase, digit, symbol."}
          </p>
        </div>
        <div>
          <label className="label" htmlFor="confirm">Confirm password</label>
          <input id="confirm" className="input" type="password" autoComplete="new-password" required aria-invalid={mismatch} value={confirm} onChange={(e) => setConfirm(e.target.value)} />
          {mismatch && <p className="mt-1 text-[11px] text-amber-300">Passwords do not match.</p>}
        </div>
        <InlineError error={error} />
        <button className="btn-primary w-full" disabled={busy || !email || !!problem || !password || mismatch}>
          {busy ? "Creating…" : "Create account"}
        </button>
      </form>
      <p className="mt-4 text-sm text-muted">
        Already registered? <Link className="link" href="/login">Sign in</Link>
      </p>
    </div>
  );
}
