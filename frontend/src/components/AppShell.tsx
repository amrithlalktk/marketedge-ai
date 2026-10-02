"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { featureOn, setAppConfig, setDefaultMarket } from "@/lib/market";
import { isAdmin, useAuth } from "@/lib/auth";
import { SAMPLE_BANNER } from "@/lib/constants";
import { useDataFlags } from "@/lib/dataflags";
import { NavLink } from "./NavLink";
import { NotificationBell } from "./Notifications";
import { cx, Loading } from "./ui";

const PUBLIC = ["/login", "/register"];

const PRIMARY = [
  { href: "/", label: "Dashboard", icon: "▦" },
  { href: "/setups", label: "Setups", icon: "◎" },
  { href: "/stocks", label: "Stocks", icon: "⌁" },
  { href: "/watchlists", label: "Watchlists", icon: "☆" },
];
const SECONDARY_ALL = [
  { href: "/portfolio", label: "Portfolio" },
  { href: "/alerts", label: "Alerts" },
  { href: "/options", label: "Options", feature: "options" },
  { href: "/news", label: "News", feature: "news" },
  { href: "/calendar", label: "Calendar", feature: "calendar" },
  { href: "/backtest", label: "Backtest", feature: "backtest" },
  { href: "/strategies", label: "Strategies", feature: "strategies" },
  { href: "/analytics", label: "Hit rates", feature: "analytics" },
  { href: "/ml", label: "ML", feature: "ml" },
  { href: "/risk", label: "Risk" },
  { href: "/account", label: "Account" },
] as const;
type Feature = Parameters<typeof featureOn>[0];
const FEATURE_PAGES: Record<string, Feature> = { "/options": "options", "/news": "news", "/calendar": "calendar", "/backtest": "backtest",
  "/strategies": "strategies", "/analytics": "analytics", "/ml": "ml" };
const pageFeature = (path: string) => Object.entries(FEATURE_PAGES).find(([p]) => path === p || path.startsWith(`${p}/`))?.[1];

function active(path: string, href: string) {
  return href === "/" ? path === "/" : path === href || path.startsWith(`${href}/`);
}

/** Clears the SAMPLE / DELAYED flags whenever the page or the selected market changes. */
function FlagReset({ path }: { path: string }) {
  const market = useSearchParams().get("market");
  const { reset } = useDataFlags();
  useEffect(() => reset(), [path, market, reset]);
  return null;
}

function Banners() {
  const { sample, delayed } = useDataFlags();
  if (!sample && !delayed) return null;
  return (
    <div className="sticky top-0 z-40">
      {sample && (
        <div role="alert" className="border-b border-fuchsia-800 bg-fuchsia-950 px-3 py-1.5 text-center text-xs font-bold tracking-wide text-fuchsia-100 sm:text-sm">
          {SAMPLE_BANNER}
        </div>
      )}
      {delayed && (
        <div role="status" className="border-b border-amber-800 bg-amber-950 px-3 py-1 text-center text-xs font-semibold text-amber-200">
          ⚠ DATA DELAYED — some instruments have stale bars; check each card&apos;s timestamp.
        </div>
      )}
    </div>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname() || "/";
  const router = useRouter();
  const { status, user, logout } = useAuth();
  const [moreOpen, setMoreOpen] = useState(false);
  const isPublic = PUBLIC.includes(path);
  // the default market (first market on real data) must be known before pages read ?market=
  const [marketReady, setMarketReady] = useState(false);
  useEffect(() => {
    if (status !== "authed" || marketReady) return;
    api.markets
      .list()
      .then((r) => {
        setAppConfig({ markets: r.items.map((m) => m.id), features: r.features });
        setDefaultMarket(r.default_market);
      })
      .catch(() => undefined) // fall back to NSE
      .finally(() => setMarketReady(true));
  }, [status, marketReady]);

  useEffect(() => {
    if (status === "anon" && !isPublic) router.replace(`/login?next=${encodeURIComponent(path)}`);
    if (status === "authed" && isPublic) router.replace("/");
  }, [status, isPublic, path, router]);

  useEffect(() => setMoreOpen(false), [path]);

  if (isPublic) {
    return (
      <div className="min-h-screen">
        <Banners />
        <main className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4 py-8">{children}</main>
      </div>
    );
  }

  const SECONDARY = SECONDARY_ALL.filter((n) => !("feature" in n) || featureOn(n.feature));
  const secondary = [...SECONDARY, ...(isAdmin(user) ? [{ href: "/admin", label: "Admin" }] : [])];
  const offFeature = marketReady ? pageFeature(path) : undefined;

  return (
    <div className="min-h-screen pb-20 xl:pb-0">
      <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-accent focus:px-3 focus:py-2 focus:text-white">
        Skip to content
      </a>
      <Banners />
      <header className="border-b border-edge bg-panel/95 backdrop-blur">
        <div className="mx-auto flex h-12 max-w-7xl items-center gap-4 px-4">
          <Link href="/" className="flex shrink-0 items-center gap-2 font-semibold tracking-tight">
            <span className="grid h-6 w-6 place-items-center rounded bg-accent text-xs font-black text-white" aria-hidden>
              M
            </span>
            <span>
              MarketEdge <span className="text-accent">AI</span>
            </span>
          </Link>
          <nav aria-label="Primary" className="hidden min-w-0 flex-1 items-center gap-0.5 xl:flex">
            {[...PRIMARY, ...secondary.slice(0, 5)].map((n) => (
              <NavLink
                key={n.href}
                href={n.href}
                aria-current={active(path, n.href) ? "page" : undefined}
                className={cx("whitespace-nowrap rounded px-2 py-1.5 text-sm", active(path, n.href) ? "bg-panel2 text-ink" : "text-muted hover:text-ink")}
              >
                {n.label}
              </NavLink>
            ))}
            <details className="relative" key={path}>
              <summary className={cx("cursor-pointer list-none whitespace-nowrap rounded px-2 py-1.5 text-sm", secondary.slice(5).some((n) => active(path, n.href)) ? "bg-panel2 text-ink" : "text-muted hover:text-ink")}>More ▾</summary>
              <ul className="absolute left-0 z-50 mt-1 w-44 rounded-md border border-edge bg-panel p-1 shadow-xl">
                {secondary.slice(5).map((n) => (
                  <li key={n.href}>
                    <NavLink href={n.href} aria-current={active(path, n.href) ? "page" : undefined} className={cx("block rounded px-2 py-1.5 text-sm", active(path, n.href) ? "bg-panel2 text-ink" : "text-muted hover:bg-panel2 hover:text-ink")}>
                      {n.label}
                    </NavLink>
                  </li>
                ))}
              </ul>
            </details>
          </nav>
          <div className="ml-auto flex items-center gap-2 text-xs text-muted">
            <NotificationBell />
            {user && (
              <span className="hidden max-w-[16rem] truncate 2xl:inline" title={user.email}>
                {user.email} · <span className="uppercase">{user.role}</span>
              </span>
            )}
            {user && (
              <button className="btn-ghost px-2 py-1 text-xs" onClick={() => logout()}>
                Sign out
              </button>
            )}
          </div>
        </div>
      </header>

      <main id="main" className="mx-auto w-full max-w-7xl px-4 py-4 sm:py-6">
        <Suspense>
          <FlagReset path={path} />
        </Suspense>
        {status === "loading" ? (
          <Loading label="Restoring session…" />
        ) : status === "anon" ? (
          <Loading label="Redirecting to sign in…" />
        ) : !marketReady ? (
          <Loading label="Loading markets…" />
        ) : offFeature && !featureOn(offFeature) ? (
          <div role="status" className="rounded-md border border-edge bg-panel p-4 text-sm">
            <p className="font-semibold">This feature is switched off</p>
            <p className="mt-1 text-muted">The app runs in simple mode. Remove “{offFeature}” from FEATURES_DISABLED in .env to bring it back.</p>
          </div>
        ) : (
          children
        )}
      </main>

      {/* Mobile bottom tab bar */}
      <nav aria-label="Primary mobile" className="fixed inset-x-0 bottom-0 z-40 border-t border-edge bg-panel/95 backdrop-blur xl:hidden">
        {moreOpen && (
          <div id="more-menu" className="border-b border-edge px-2 py-2">
            <ul className="grid grid-cols-2 gap-1">
              {secondary.map((n) => (
                <li key={n.href}>
                  <NavLink href={n.href} className={cx("block rounded px-3 py-2 text-sm", active(path, n.href) ? "bg-panel2 text-ink" : "text-muted")}>
                    {n.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        )}
        <ul className="grid grid-cols-5">
          {PRIMARY.map((n) => (
            <li key={n.href}>
              <NavLink
                href={n.href}
                aria-current={active(path, n.href) ? "page" : undefined}
                className={cx("flex flex-col items-center gap-0.5 py-2 text-[11px]", active(path, n.href) ? "text-accent" : "text-muted")}
              >
                <span aria-hidden className="text-base leading-none">
                  {n.icon}
                </span>
                {n.label}
              </NavLink>
            </li>
          ))}
          <li>
            <button
              type="button"
              aria-expanded={moreOpen}
              aria-controls="more-menu"
              onClick={() => setMoreOpen((o) => !o)}
              className={cx("flex w-full flex-col items-center gap-0.5 py-2 text-[11px]", moreOpen || secondary.some((s) => active(path, s.href)) ? "text-accent" : "text-muted")}
            >
              <span aria-hidden className="text-base leading-none">
                ⋯
              </span>
              More
            </button>
          </li>
        </ul>
      </nav>
    </div>
  );
}
