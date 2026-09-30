# MarketEdge AI — frontend

Next.js 15 (App Router) + TypeScript + Tailwind. Charts use TradingView **lightweight-charts** (Apache-2.0).
This is an analysis tool: every rate is shown with its sample size and period, and every signal/backtest page carries the disclaimer.

## Run locally

```bash
cp .env.example .env.local      # API_URL=http://localhost:8000
npm install
npm run dev                     # http://localhost:3000
```

The backend must be running (`../scripts/dev-local.sh`). Dev admin: `admin@example.com` / `Adm1n!Password`.

Scripts: `dev`, `build`, `start`, `lint`, `typecheck`.

## How API access works

- The browser only calls same-origin `/api/v1/*`; `next.config.mjs` rewrites it to `API_URL`. This keeps the
  `SameSite=Strict`, httpOnly refresh cookie (path `/api/v1/auth`) working and avoids CORS.
- The access token is held in memory only (`src/lib/api.ts` + `AuthProvider`). On load the app calls
  `POST /auth/refresh`; on a 401 it refreshes once (single-flight, serialised across tabs via Web Locks) and retries.
- Rewrites are resolved at **build time**: set `API_URL` when running `next build` / building the image.

## Docker

```bash
docker build --build-arg API_URL=http://backend:8000 -t marketedge-frontend .
docker run -p 3000:3000 marketedge-frontend
```

Multi-stage, `output: "standalone"`, runs as non-root `nextjs` on port 3000.

## Layout

- `src/app/*` — routes (`/`, `/setups`, `/setups/[id]`, `/stocks`, `/stocks/[symbol]`, `/watchlists`, `/backtest`, `/risk`, `/admin`, `/account`, `/login`, `/register`)
- `src/components/*` — UI primitives, charts, setup/backtest panels
- `src/lib/api.ts` typed client · `src/lib/types.ts` backend payload types · `src/lib/format.ts` INR/number formatting
