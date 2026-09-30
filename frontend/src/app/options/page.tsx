"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";
import { ChainTab } from "@/components/options/ChainTab";
import { BuilderTab, SetupsTab, StrategiesTab } from "@/components/options/EngineTabs";
import { OverviewTab } from "@/components/options/OverviewTab";
import { OptionsDisclaimer } from "@/components/options/parts";
import { DataStamp, ErrorState, PageHeader, Segmented, Skeleton } from "@/components/ui";
import { ApiError, api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { num } from "@/lib/format";
import type { OptionsOverview } from "@/lib/types";
import { useApi } from "@/lib/useApi";

type Tab = "overview" | "chain" | "setups" | "strategies" | "builder";
const TABS: { value: Tab; label: string }[] = [
  { value: "overview", label: "Overview" },
  { value: "chain", label: "Option chain" },
  { value: "setups", label: "Setups" },
  { value: "strategies", label: "Strategies" },
  { value: "builder", label: "Payoff builder" },
];

function OptionsInner() {
  const { can } = useAuth();
  const params = useSearchParams();
  const router = useRouter();
  const raw = params.get("tab") as Tab | null;
  const tab: Tab = TABS.some((t) => t.value === raw) ? (raw as Tab) : "overview";
  const allowed = can("options:read");
  const ov = useApi<OptionsOverview>(() => api.options.nifty(), [], allowed);
  const setTab = (t: Tab) => router.replace(t === "overview" ? "/options" : `/options?tab=${t}`, { scroll: false });

  if (!allowed) {
    return <ErrorState error={new ApiError(403, "Missing permission: options:read")} what="NIFTY options" />;
  }
  const d = ov.data;
  const tradable = d ? d.expiries.filter((e) => e >= d.oi.expiry) : [];

  return (
    <>
      <PageHeader
        title="NIFTY options"
        subtitle={d ? <>Spot <span className="num text-ink">{num(d.underlying.spot)}</span> · lot {d.underlying.lot_size} · options analysis, not advice</> : "Market state, option chain, setups and strategies"}
        right={d ? <DataStamp meta={d.data} asOf={d.underlying.as_of} /> : null}
      />
      <div className="mb-4 max-w-full">
        <Segmented<Tab> label="Options section" value={tab} onChange={setTab} options={TABS} />
      </div>
      {ov.error && <ErrorState error={ov.error} onRetry={ov.reload} what="options analysis" />}
      {ov.loading && !d && <Skeleton className="h-96" />}
      {d && (
        <>
          {tab === "overview" && <OverviewTab ov={d} />}
          {tab === "chain" && <ChainTab expiries={d.expiries} defaultExpiry={d.oi.expiry} />}
          {tab === "setups" && <SetupsTab />}
          {tab === "strategies" && <StrategiesTab spot={d.underlying.spot} />}
          {tab === "builder" && <BuilderTab expiries={tradable.length ? tradable : d.expiries} defaultExpiry={d.oi.expiry} />}
        </>
      )}
      <OptionsDisclaimer text={d?.disclaimer} />
    </>
  );
}

export default function OptionsPage() {
  return (
    <Suspense>
      <OptionsInner />
    </Suspense>
  );
}
