"use client";

import { PositionSizer } from "@/components/PositionSizer";
import { Card, Disclaimer, PageHeader } from "@/components/ui";

export default function RiskPage() {
  return (
    <>
      <PageHeader title="Risk & position sizing" subtitle="Size positions from the amount you are prepared to lose, not from a target." />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card title="Position-size calculator" className="lg:col-span-2">
          <PositionSizer entry={1000} stop={950} />
        </Card>
        <Card title="How it works">
          <ul className="space-y-2 text-sm text-muted">
            <li><strong className="text-ink">Max risk</strong> = capital × risk %. Example: ₹5,00,000 × 1% = ₹5,000.</li>
            <li><strong className="text-ink">Risk / unit</strong> = |entry − stop|. Example: 1000 − 950 = ₹50.</li>
            <li><strong className="text-ink">Quantity</strong> = max risk ÷ risk per unit, rounded down to the lot size → 100 shares.</li>
            <li><strong className="text-ink">ATR-based</strong>: stop = entry ∓ ATR × multiple, then sized the same way.</li>
            <li>Quantity is also capped by available capital and the optional max-position %.</li>
            <li>Gaps and slippage can make realised losses exceed the planned maximum.</li>
          </ul>
        </Card>
      </div>
      <Disclaimer />
    </>
  );
}
