"use client";

import Link from "next/link";
import { Suspense, type ComponentProps } from "react";
import { useMarket, withMarket } from "@/lib/market";

/** Pages that follow the global market selection. */
const MARKET_PAGES = new Set(["/", "/setups", "/stocks", "/analytics", "/backtest"]);

function Inner({ href, ...rest }: ComponentProps<typeof Link> & { href: string }) {
  const [market] = useMarket();
  return <Link href={MARKET_PAGES.has(href) ? withMarket(href, market) : href} {...rest} />;
}

/** Nav link that carries `?market=` to market-aware pages (Suspense-wrapped for static rendering). */
export function NavLink(props: ComponentProps<typeof Link> & { href: string }) {
  return (
    <Suspense fallback={<Link {...props} />}>
      <Inner {...props} />
    </Suspense>
  );
}
