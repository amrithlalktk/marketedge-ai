"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import { useAuth } from "./auth";
import { scanFlags, useDataFlags } from "./dataflags";

export interface ApiState<T> {
  data: T | null;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
  setData: (d: T | null) => void;
}

function toApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e;
  return new ApiError(0, e instanceof Error ? e.message : "Network error — is the API reachable?");
}

/**
 * Fetches once the auth session is ready, reports sample/delayed flags to the global banner,
 * and re-runs when `deps` change. Pass `enabled=false` to skip (e.g. permission not granted).
 */
export function useApi<T>(fn: () => Promise<T>, deps: unknown[], enabled = true): ApiState<T> {
  const { status } = useAuth();
  const { report } = useDataFlags();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState<boolean>(enabled);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (status !== "authed" || !enabled) {
      if (!enabled) setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    fnRef
      .current()
      .then((d) => {
        if (cancelled) return;
        setData(d);
        report(scanFlags(d));
      })
      .catch((e) => {
        if (!cancelled) setError(toApiError(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, enabled, tick, ...deps]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  const set = useCallback(
    (d: T | null) => {
      setData(d);
      if (d) report(scanFlags(d));
    },
    [report],
  );
  return { data, error, loading, reload, setData: set };
}

export { toApiError };
