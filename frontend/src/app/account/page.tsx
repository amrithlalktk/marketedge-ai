"use client";

import { useEffect, useState } from "react";
import { NotificationSettingsPanel } from "@/components/NotificationSettings";
import { Card, Disclaimer, InlineError, PageHeader, Pill } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import type { TotpSetup } from "@/lib/types";

function Qr({ uri }: { uri: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    import("qrcode").then((q) => q.toDataURL(uri, { margin: 1, width: 200, color: { dark: "#000000", light: "#ffffff" } })).then((d) => live && setSrc(d)).catch(() => setSrc(null));
    return () => { live = false; };
  }, [uri]);
  // eslint-disable-next-line @next/next/no-img-element
  return src ? <img src={src} width={200} height={200} alt="QR code for your authenticator app" className="rounded bg-white p-1" /> : <div className="h-[200px] w-[200px] animate-pulse rounded bg-panel2" aria-hidden />;
}

export default function AccountPage() {
  const { user, reloadUser, logout } = useAuth();
  const [setup, setSetup] = useState<TotpSetup | null>(null);
  const [code, setCode] = useState("");
  const [pw, setPw] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!user) return null;

  const run = async (fn: () => Promise<void>) => {
    setError(null); setOk(null); setBusy(true);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : "Request failed"); } finally { setBusy(false); }
  };

  return (
    <>
      <PageHeader title="Account" subtitle="Profile and security settings" />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Profile">
          <dl className="space-y-2 text-sm">
            <div className="flex justify-between gap-2"><dt className="text-muted">Name</dt><dd>{user.full_name || "—"}</dd></div>
            <div className="flex justify-between gap-2"><dt className="text-muted">Email</dt><dd className="truncate">{user.email}</dd></div>
            <div className="flex justify-between gap-2"><dt className="text-muted">Plan / role</dt><dd><Pill tone="blue">{user.role}</Pill></dd></div>
            <div><dt className="text-muted">Permissions</dt><dd className="mt-1 flex flex-wrap gap-1">{user.permissions.map((p) => <Pill key={p}>{p}</Pill>)}</dd></div>
          </dl>
          <button className="btn-ghost mt-4" onClick={() => logout()}>Sign out of this device</button>
        </Card>

        <Card title="Two-factor authentication" right={user.totp_enabled ? <Pill tone="green">Enabled</Pill> : <Pill tone="amber">Disabled</Pill>}>
          {!user.totp_enabled && !setup && (
            <>
              <p className="text-sm text-muted">Protect your account with a time-based one-time code (TOTP) from an authenticator app.</p>
              <button className="btn-primary mt-3" disabled={busy} onClick={() => run(async () => setSetup(await api.auth.totpSetup()))}>Set up 2FA</button>
            </>
          )}
          {!user.totp_enabled && setup && (
            <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); run(async () => { await api.auth.totpEnable(code.trim()); await reloadUser(); setSetup(null); setCode(""); setOk("Two-factor authentication is now enabled."); }); }}>
              <p className="text-sm">1. Scan this QR code with your authenticator app, or enter the secret manually.</p>
              <Qr uri={setup.otpauth_uri} />
              <div>
                <span className="label">Secret</span>
                <code className="block break-all rounded border border-edge bg-bg px-2 py-1 font-mono text-sm">{setup.secret}</code>
              </div>
              <details className="text-xs text-muted"><summary className="cursor-pointer">Show otpauth URI</summary><code className="mt-1 block break-all font-mono">{setup.otpauth_uri}</code></details>
              <div>
                <label className="label" htmlFor="code">2. Enter the 6-digit code to confirm</label>
                <input id="code" className="input num tracking-widest" inputMode="numeric" autoComplete="one-time-code" maxLength={10} value={code} onChange={(e) => setCode(e.target.value)} />
              </div>
              <div className="flex gap-2">
                <button className="btn-primary" disabled={busy || code.trim().length < 6}>Enable 2FA</button>
                <button type="button" className="btn-ghost" onClick={() => { setSetup(null); setCode(""); }}>Cancel</button>
              </div>
            </form>
          )}
          {user.totp_enabled && (
            <form className="space-y-3" onSubmit={(e) => { e.preventDefault(); run(async () => { await api.auth.totpDisable(code.trim(), pw); await reloadUser(); setCode(""); setPw(""); setOk("Two-factor authentication disabled."); }); }}>
              <p className="text-sm text-muted">To disable 2FA, confirm your password and a current code.</p>
              <div>
                <label className="label" htmlFor="pw">Password</label>
                <input id="pw" type="password" className="input" autoComplete="current-password" value={pw} onChange={(e) => setPw(e.target.value)} />
              </div>
              <div>
                <label className="label" htmlFor="dcode">Authenticator code</label>
                <input id="dcode" className="input num tracking-widest" inputMode="numeric" autoComplete="one-time-code" maxLength={10} value={code} onChange={(e) => setCode(e.target.value)} />
              </div>
              <button className="btn-danger" disabled={busy || !pw || code.trim().length < 6}>Disable 2FA</button>
            </form>
          )}
          {ok && <p role="status" className="mt-3 rounded border border-green-900 bg-green-950/40 px-2 py-1.5 text-sm text-green-200">{ok}</p>}
          <InlineError error={error} />
        </Card>
      </div>
      <div className="mt-4"><NotificationSettingsPanel /></div>
      <Disclaimer />
    </>
  );
}
