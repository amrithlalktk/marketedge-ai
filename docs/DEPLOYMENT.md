# MarketEdge AI (lite): deploy on Vercel + Neon + GitHub Actions

This guide sets up a free hosting of MarketEdge AI for one user. It is written for whoever does the setup, and assumes basic familiarity with GitHub and a web browser.

## How the pieces fit

| Piece | Runs on | What it does |
|---|---|---|
| Website (Next.js) + API (FastAPI) | **One Vercel project** (Vercel Services, configured by the root `vercel.json`) | The site serves the pages and proxies `/api/v1/*` to the API. The API is a **private** service: only the website can reach it. |
| Database | **Neon** Postgres (free 0.5 GB) | Everything: prices, scans, users and encrypted provider keys. |
| Daily pipeline | **GitHub Actions** (`.github/workflows/daily.yml`) | Runs NSE at 18:30 IST on weekdays (prices → scan → NIFTY options → daily ideas message) and crypto at 06:00 IST every day. |

There are no servers, queues or always-on workers. The free tiers are enough:

- **Database:** about 300 NSE stocks with 4 years of history, plus 40 coins, comes to about 100 MB.
- **GitHub Actions:** the daily runs take about 10 minutes, roughly 400 of the 2,000 free minutes a month.

## Before you start

### 1. Generate the secrets
Run this on your computer:

```sh
python3 -c "import secrets;print('SECRET_KEY=' + secrets.token_hex(32))"
python3 -c "import base64,os;print('ENCRYPTION_KEY=' + base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

Keep both values. **Use the same values in Vercel and GitHub.** The encryption key protects your Upstox token in the database, so if the two places don't match, the daily job can't read the token.

### 2. Create a Neon database
1. Create a project on neon.tech (or add **Neon** from Vercel → Storage).
2. Copy the **pooled** connection string.
3. Change its start to `postgresql+psycopg://…` and keep `?sslmode=require`. For example:

   `postgresql+psycopg://USER:PASSWORD@ep-xxx-pooler.REGION.aws.neon.tech/neondb?sslmode=require`

## Step 1: GitHub secrets
In the repo, go to Settings → Secrets and variables → Actions.

| Secret | Value |
|---|---|
| `DATABASE_URL` | the Neon connection string from above |
| `SECRET_KEY` | from step 1 |
| `ENCRYPTION_KEY` | from step 1 |
| `BOOTSTRAP_ADMIN_EMAIL` | your login email |
| `BOOTSTRAP_ADMIN_PASSWORD` | a strong password: at least 10 characters, with upper and lower case, a digit and a symbol |

**Variables** (same page, Variables tab):

| Variable | Value |
|---|---|
| `DAILY_ENABLED` | `true`. Turns on the schedule; until then only manual runs happen, so nothing fails while you're still setting up. |
| `PUBLIC_APP_URL` | optional: your website address, used in notification links |

The provider defaults already suit Upstox and Binance.  

## Step 2: One Vercel project (website + API)
1. Vercel → **Add New → Project** → import this repo. Leave **Root Directory empty** (the repo root). The root `vercel.json` defines two services: `frontend` (Next.js) and `backend` (FastAPI `app.main:app`). The website reaches the API through a private binding (`BACKEND_URL`).
2. Add a Neon database: in the project, go to **Storage → Create → Neon**, or paste your own URL. Any of `postgres://`, `postgresql://` or `postgresql+psycopg://` works.
3. Add these environment variables (they apply to both services):

```
DATABASE_URL=<Neon pooled URL>
DB_NULL_POOL=true
SECRET_KEY=<same as GitHub>
ENCRYPTION_KEY=<same as GitHub>
ENVIRONMENT=production
COOKIE_SECURE=true
MARKETS_ENABLED=NSE,CRYPTO
MARKET_DATA_PROVIDER=upstox
OPTIONS_DATA_PROVIDER=upstox
CRYPTO_DATA_PROVIDER=binance
BENCHMARK_SYMBOL=NIFTY50
VIX_SYMBOL=INDIAVIX
OPTIONS_UNDERLYING=NIFTY50
OPTIONS_DISPLAY_NAME=NIFTY 50
BENCHMARK_CRYPTO=BTCUSDT
ALLOW_REGISTRATION=false
JOB_RUNNER=github
GITHUB_REPO=<owner>/<repo>
GITHUB_DISPATCH_TOKEN=<fine-grained token, see below>
CRON_SECRET=<random string, see below>
CORS_ORIGINS=https://<your-app>.vercel.app
PUBLIC_APP_URL=https://<your-app>.vercel.app
UPSTOX_REDIRECT_URI=https://<your-app>.vercel.app/api/v1/upstox/callback
```

4. **Deploy**. To check it worked, open `https://<your-app>.vercel.app/api/v1/auth/config`; it should answer `{"registration_open": false}`.

If you already deployed the website as its own project with Root Directory `frontend`, change that project's **Root Directory** to empty (Settings → Build and Deployment) and redeploy, or import the repo again as a new project.

**`GITHUB_DISPATCH_TOKEN`** is what the app's Admin → Jobs → "Run now" button uses to start the daily workflow. To create it:

1. Go to GitHub → Settings → Developer settings → Fine-grained tokens.
2. Limit it to **only this repository**.
3. Give it the permission **Actions: Read and write**.

The same token lets **Vercel Cron** start the daily runs on time (see `crons` in `vercel.json`: NSE 18:30 IST weekdays,
crypto 06:00 IST). GitHub's own schedule is only a backup: it often starts hours late, and it skips a market that
already ran. Vercel's free plan fires a cron somewhere within the scheduled hour.

**`CRON_SECRET`** protects the cron endpoint so nobody else can start runs. Make one with
`python3 -c "import secrets; print(secrets.token_urlsafe(32))"` and add it in Vercel only (not GitHub).

Without these two, the GitHub schedule still runs the jobs, just possibly hours late.

## Step 3: First run (creates the tables, your admin account and the data)
1. GitHub → **Actions** → **daily** → **Run workflow**, with market **CRYPTO** and full **true**. Binance data needs no key, and this first run also creates the database tables and your admin account. It takes about 5 minutes.
2. Sign in to the website with your bootstrap email and password.
3. Go to **Admin → Providers → Upstox** and paste your **analytics token**. It is checked with Upstox and stored encrypted in Neon.
4. Run the workflow again with market **NSE** and full **true** (about 10 minutes). The token is needed to pick the 300 most-traded stocks and to read the NIFTY option chain.

After that, everything is automatic. The website shows Today's trade ideas after each run, and the daily message arrives in the app's notification bell.

## Notes and limits
- **Upstox data:** the analytics token is read-only, valid for a year and personal-use. Keep sign-up closed (`ALLOW_REGISTRATION=false`) so nobody else can see your data. Don't click "Generate Token" again unless you intend to replace it, because that revokes the old one.
- **NSE pool:** 300 stocks, chosen by traded value. A stock that later enters the top 300 is added; stocks already in the pool are never dropped. Set `UPSTOX_UNIVERSE_SIZE` to change the size.
- **Crypto:** today's top 40 coins only. The survivorship-free 678-coin pool (`BINANCE_PIT_UNIVERSE=true`) needs about 10× the database space.
- **Alerts:** price alerts are checked once a day, after each market's run, not every 5 minutes.
- **Telegram:** set the webhook to `https://<your-website>/api/v1/notifications/telegram/webhook`. There's no polling on serverless.
- **Local development:** `docker compose up --build` gives you postgres, the API and the website, with sample data. Jobs run from Admin → Jobs.
