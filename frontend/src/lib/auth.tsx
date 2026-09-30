"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api, refreshSession, setAccessToken, setOnAuthLost } from "./api";
import type { User } from "./types";

type Status = "loading" | "authed" | "anon";

interface AuthCtx {
  status: Status;
  user: User | null;
  can: (perm: string) => boolean;
  login: (email: string, password: string, totp?: string) => Promise<User>;
  logout: () => Promise<void>;
  reloadUser: () => Promise<void>;
}

const Ctx = createContext<AuthCtx | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<Status>("loading");
  const [user, setUser] = useState<User | null>(null);
  const started = useRef(false);

  useEffect(() => {
    setOnAuthLost(() => {
      setUser(null);
      setStatus("anon");
    });
    return () => setOnAuthLost(null);
  }, []);

  // Obtain an access token on load from the httpOnly refresh cookie. Guarded so StrictMode's double
  // effect invocation never sends the rotating refresh token twice.
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      const tok = await refreshSession();
      if (!tok) {
        setStatus("anon");
        return;
      }
      try {
        setUser(await api.auth.me());
        setStatus("authed");
      } catch {
        setAccessToken(null);
        setStatus("anon");
      }
    })();
  }, []);

  const login = useCallback(async (email: string, password: string, totp?: string) => {
    await api.auth.login(email, password, totp);
    const me = await api.auth.me();
    setUser(me);
    setStatus("authed");
    return me;
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      setAccessToken(null);
      setUser(null);
      setStatus("anon");
    }
  }, []);

  const reloadUser = useCallback(async () => {
    setUser(await api.auth.me());
  }, []);

  const value = useMemo<AuthCtx>(
    () => ({ status, user, can: (p) => !!user?.permissions.includes(p), login, logout, reloadUser }),
    [status, user, login, logout, reloadUser],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAuth must be used inside AuthProvider");
  return v;
}

export function isAdmin(user: User | null): boolean {
  return !!user?.permissions.some((p) => p.startsWith("admin:") || p === "audit:read");
}
