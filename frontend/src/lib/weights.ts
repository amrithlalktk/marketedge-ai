"use client";

import { useEffect, useState } from "react";
import { api } from "./api";
import { useAuth } from "./auth";

let cache: Promise<Record<string, number> | null> | null = null;

/** Configured score weights (admins only; others get null). Cached for the session, one request. */
export function useEngineWeights(): Record<string, number> | null {
  const { can, status } = useAuth();
  const allowed = status === "authed" && can("admin:settings");
  const [w, setW] = useState<Record<string, number> | null>(null);
  useEffect(() => {
    if (!allowed) return;
    cache ??= api.admin.getEngine().then((r) => r.effective.weights).catch(() => null);
    let live = true;
    cache.then((x) => live && setW(x));
    return () => {
      live = false;
    };
  }, [allowed]);
  return allowed ? w : null;
}

export function invalidateWeights() {
  cache = null;
}
