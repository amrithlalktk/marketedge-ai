"use client";

import { createContext, useCallback, useContext, useMemo, useState } from "react";

export interface Flags {
  sample: boolean;
  delayed: boolean;
}

interface FlagsCtx extends Flags {
  report: (f: Partial<Flags>) => void;
  /** clear the flags (called on every page/market change so the banner describes the CURRENT page only) */
  reset: () => void;
}

const Ctx = createContext<FlagsCtx | null>(null);

/** Walks a payload looking for is_sample / is_sample_data / data.delayed. Bounded so big bar arrays stay cheap. */
export function scanFlags(obj: unknown, depth = 0): Flags {
  const out: Flags = { sample: false, delayed: false };
  const visit = (o: unknown, d: number) => {
    if (d > 5 || o === null || typeof o !== "object" || (out.sample && out.delayed)) return;
    if (Array.isArray(o)) {
      for (let i = 0; i < Math.min(o.length, 25); i++) {
        const x = o[i];
        if (x !== null && typeof x === "object") visit(x, d + 1);
        else return; // arrays of primitives (prices, dates) carry no flags
      }
      return;
    }
    const r = o as Record<string, unknown>;
    if (r.is_sample === true || r.is_sample_data === true) out.sample = true;
    if (r.delayed === true) out.delayed = true;
    for (const k in r) {
      const v = r[k];
      if (v !== null && typeof v === "object") visit(v, d + 1);
    }
  };
  visit(obj, depth);
  return out;
}

export function DataFlagsProvider({ children }: { children: React.ReactNode }) {
  const [flags, setFlags] = useState<Flags>({ sample: false, delayed: false });
  // Flags are sticky per page view: once any payload on the page was sample/delayed the banner stays up
  // until the user navigates or switches market (AppShell calls reset()).
  const reset = useCallback(() => setFlags((prev) => (prev.sample || prev.delayed ? { sample: false, delayed: false } : prev)), []);
  const report = useCallback((f: Partial<Flags>) => {
    setFlags((prev) => {
      const next = { sample: prev.sample || !!f.sample, delayed: prev.delayed || !!f.delayed };
      return next.sample === prev.sample && next.delayed === prev.delayed ? prev : next;
    });
  }, []);
  const value = useMemo(() => ({ ...flags, report, reset }), [flags, report, reset]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useDataFlags(): FlagsCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useDataFlags must be used inside DataFlagsProvider");
  return v;
}
