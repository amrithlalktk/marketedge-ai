"use client";

import { useState } from "react";
import { NotificationRow, emitNotificationsChanged } from "@/components/Notifications";
import { Card, EmptyState, ErrorState, Loading, PageHeader, Segmented } from "@/components/ui";
import { api } from "@/lib/api";
import type { NotificationItem } from "@/lib/types";
import { useApi } from "@/lib/useApi";

type F = "all" | "unread";

export default function NotificationsPage() {
  const [f, setF] = useState<F>("all");
  const q = useApi<{ items: NotificationItem[]; unread: number }>(() => api.notifications.list({ unread: f === "unread", limit: 200 }), [f]);
  const markAll = async () => { await api.notifications.read(); q.reload(); emitNotificationsChanged(); };
  return (
    <>
      <PageHeader title="Notifications" subtitle="Alert triggers, setup updates and test messages, with per-channel delivery status." />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Segmented<F> label="Filter" value={f} onChange={setF} options={[{ value: "all", label: "All" }, { value: "unread", label: "Unread" }]} />
        <button type="button" className="btn-ghost px-2 py-1 text-xs" disabled={!q.data?.unread} onClick={markAll}>Mark all read</button>
        {q.data && <span className="text-xs text-muted">{q.data.unread} unread</span>}
      </div>
      <Card title={`${f === "unread" ? "Unread" : "Latest"} (${q.data?.items.length ?? 0})`}>
        {q.error ? <ErrorState error={q.error} onRetry={q.reload} what="notifications" /> : !q.data ? <Loading /> : q.data.items.length === 0 ? <EmptyState title={f === "unread" ? "Nothing unread" : "No notifications yet"}>Create an alert to get notified when it triggers.</EmptyState> : (
          <div className="divide-y divide-edge">{q.data.items.map((n) => <NotificationRow key={n.id} n={n} onOpen={() => { if (!n.read) api.notifications.read([n.id]).then(emitNotificationsChanged); }} />)}</div>
        )}
      </Card>
      <p className="mt-3 text-[11px] text-muted">Notifications are informational; they are generated from end-of-day data unless an intraday feed is configured and do not constitute advice.</p>
    </>
  );
}
