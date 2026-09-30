"use client";

import Link from "next/link";
import { cx } from "./ui";

export const CHANNEL_LABELS: Record<string, string> = { web: "In-app", email: "Email", telegram: "Telegram", push: "Web push", whatsapp: "WhatsApp" };
export function ChannelPicker({ value, onChange, channels, idp }: { value: string[]; onChange: (v: string[]) => void; channels: Record<string, { configured: boolean }>; idp: string }) {
  return (
    <fieldset>
      <legend className="label">Channels</legend>
      <div className="flex flex-wrap gap-1.5">
        {Object.keys(CHANNEL_LABELS).filter((c) => c in channels).map((c) => {
          const ok = channels[c]?.configured;
          return (
            <label key={c} className={cx("inline-flex items-center gap-1 rounded border px-2 py-1 text-xs", ok ? "border-edge" : "border-edge/50 text-muted")} title={ok ? undefined : "Not configured by the administrator"}>
              <input id={`${idp}-${c}`} type="checkbox" disabled={!ok || c === "web"} checked={c === "web" || value.includes(c)} onChange={(e) => onChange(e.target.checked ? [...value, c] : value.filter((x) => x !== c))} />
              {CHANNEL_LABELS[c]}{!ok && <span className="text-[10px]">(not configured)</span>}
            </label>
          );
        })}
      </div>
      <p className="mt-1 text-[11px] text-muted">In-app notifications are always on. <Link className="link" href="/account#notifications">Notification settings →</Link></p>
    </fieldset>
  );
}

