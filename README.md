# EVHunter

A private sports-odds terminal for **positive expected value, arbitrage, fair odds, Kelly sizing, and bet tracking**.

Next.js + TypeScript frontend, FastAPI API and collection worker, PostgreSQL, and Redis. The local demo uses SQLite and an in-memory login limiter so you can run it without paying for data or starting infrastructure.

**The default workspace is a clearly labeled synthetic demo. Demo and live quotes, settings, bets, and bankroll records are isolated. Live connection failures never fall back to invented opportunities.**

## Run it

Install Node.js 22+ and Python 3.11+ first. These commands work from PowerShell, Command Prompt, or a Unix terminal:

```sh
git clone https://github.com/PlasmaZero/EVHunter.git
cd EVHunter
npm ci
npm run setup
npm run dev
```

Open **http://localhost:3000**.

Local demo sign-in:

```text
Username: daniel
Password: evhunter-demo-2026
```

`npm run setup` creates `.venv`, installs the pinned Python dependencies, and copies `.env.example` to `.env` if it does not already exist. It preserves an existing `.env`.

`npm run dev` starts the API on port 8000 and Next.js on port 3000. The API runs the local collection task automatically. Ctrl+C stops both processes. Override `API_PORT` or `PORT` if needed and update `ALLOWED_ORIGINS` when changing the web origin.

If the setup script cannot find Python, set `PYTHON_EXE` to its executable or create the virtual environment manually:

```powershell
# Windows
py -m venv .venv
.venv\Scripts\python -m pip install -c backend/requirements.lock -e "./backend[dev]"
Copy-Item .env.example .env
```

```sh
# macOS / Linux
python3 -m venv .venv
.venv/bin/python -m pip install -c backend/requirements.lock -e './backend[dev]'
cp .env.example .env
```

Do not overwrite an existing `.env` containing your settings.

## What works

- Searchable, sortable opportunity table with sport, venue, market, type, and minimum-edge filters.
- Pregame arbitrage detection, conservative cent rounding, liquidity caps, venue cash limits, and stake splits.
- +EV against a configurable complete reference market, using proportional, power, or Shin devigging.
- Independent no-vig, Kelly, and arbitrage calculators.
- Fractional Kelly suggestions bounded by per-bet caps and available tracked cash.
- Interactive same-line odds history and realized-versus-predicted profit charts.
- Accepted-bet logging with immutable server-side opportunity snapshots, actual prices and fees, manual settlement, bankroll adjustments, and CSV export.
- In-app threshold-crossing alerts; SMTP delivery in live mode when configured.
- Source health, a local credit budget, and reviewed prediction-market mapping management.
- Single-owner authentication, signed HttpOnly sessions, CSRF tokens, origin checks, login throttling, and production configuration validation.

The app reads market data and records bets you have already placed. It does not submit wagers, connect to sportsbook account sessions, or hold funds.

## Live data

### 1. Configure the sportsbook feed

Get an API key from [The Odds API](https://the-odds-api.com/). Its paid coverage currently lists FanDuel, DraftKings, BetMGM, Fanatics, Caesars, Novig, ProphetX, and Pinnacle. Coverage varies by sport and market. API access and data-use rights are governed by the provider agreement.

In your **untracked** `.env`:

```dotenv
DATA_MODE=live
ODDS_API_KEY=your-real-key
POLL_SECONDS=60
SPORTS=basketball_nba,americanfootball_nfl
```

Read the current full-game settlement rules for the venues you intend to compare. Then explicitly list reviewed feed books:

```dotenv
REVIEWED_BOOKS=fanduel,draftkings,betmgm,fanatics,williamhill_us
```

Caesars uses the provider key `williamhill_us`. Unreviewed feed quotes remain visible in coverage counts but cannot enter opportunity calculations. Pinnacle is initially reference-only; its aggregated odds may be delayed.

**Novig and ProphetX are excluded from live calculations until their fee treatment is configured.** Do not assume either is fee-free. `EXCHANGE_FEE_CONFIG` accepts reviewed profit-commission or verified zero-fee sportsbook-style returns, for example:

```dotenv
# Example structure only: supply the current verified rate and actual review date.
EXCHANGE_FEE_CONFIG={"novig":{"model":"profit","rate":0.02,"reviewed_at":"YYYY-MM-DD"}}
```

The sample rate is not a statement about Novig's current fee schedule. Unsupported fee models must remain excluded. Prediction-contract fee schedules should use a direct, reviewed mapping instead.

Restart the app after changing environment variables. Live mode starts with **zero tracked cash**. Use **Bet tracker → Deposit / withdrawal** to record your bankroll; this only updates your ledger. +EV prices can be displayed without cash, but sizing is zero and arbitrage allocations require available bankroll.

### 2. Map Kalshi and Polymarket contracts

Direct integrations use public read endpoints:

| Venue | Adapter | Execution treatment |
| --- | --- | --- |
| Kalshi | REST market metadata and order-book snapshots; complementary YES/NO bids become executable asks | Requires a reviewed matching contract, fee model, and venue eligibility |
| Polymarket US | Separate public gateway market books; offers and complementary bids | Requires a reviewed matching US contract and fee model |
| Polymarket international | CLOB order books for two explicitly supplied outcome token IDs | Reference-only in this US-focused MVP |

After sportsbook markets are ingested, open **Data sources → Map a market**. Select the canonical event and exact line, identify the external contract, map its YES outcome, enter the reviewed fee model, and confirm matching rules. No fuzzy name match can silently authorize a cross-venue arbitrage.

For international Polymarket, enter the condition ID and the YES/NO token IDs. The adapter verifies the order books' condition IDs when supplied. For US books, a suspended or non-open state is excluded. Kalshi snapshots must refer to an active market.

Direct adapters poll at 20 seconds by default. They intentionally use top-of-book available size and whole-contract quantities. When a venue does not provide a source timestamp, the quote explicitly records a snapshot-receipt timestamp basis. Those timestamps are not represented as venue-originated updates.

The MVP limits each prediction venue to 20 reviewed mappings. Higher-throughput discovery, streaming connections, and deeper-book optimization are phase-2 work.

### 3. Set your strategy

In **Settings**, select permitted execution venues, jurisdiction, reference source, devig method, minimum edges, Kelly fraction, stake caps, freshness limits, and alerts. Live additions to execution venues require an explicit eligibility review. This is your recorded review, not a geolocation service or legal determination.

A complete reference outcome set is required. If its price is missing, stale, or cannot be devigged, the engine emits no +EV signal for that reference. It never silently switches devig methods.

### 4. Email alerts

Set the SMTP variables in `.env`, then enable email alerts and enter your own recipient address in Settings. SMTP credentials are never returned to the browser. SMTP delivery is disabled in demo mode. Alerts are queued, deduplicated for ten minutes, retried with bounded backoff, and discarded when their underlying quotes expire.

## Pricing and matching rules

Canonical identity includes event, market type, exact home spread or total line, complete outcome set, and settlement identity. The first version supports NBA full-game moneyline and verified half-point NBA/NFL spreads and totals. NFL moneylines, integer lines, player props, and ambiguous settlement cases are excluded.

For mutually exclusive, exhaustive no-push outcomes with effective decimal returns `d[i]`:

```text
implied total Q = sum(1 / d[i])
arbitrage when Q < 1
ideal stake[i] = budget / (d[i] * Q)
ideal ROI = 1 / Q - 1
```

Actual allocations are recalculated with cent rounding, whole contracts, entry fees, win commissions, available depth, and optional venue cash balances. Reported net ROI uses the actual total allocation and minimum modeled return. A missing acceptance limit stays labeled unverified. Every leg must be accepted and settle under the matched rules before profit can be considered locked in.

For a binary no-push cash bet:

```text
EV = p * effective_decimal_return - 1
full Kelly = max(0, (p * effective_decimal_return - 1) / (effective_decimal_return - 1))
```

Fair probabilities are estimates. Proportional devig divides implied probabilities by their sum; power solves a normalization exponent; Shin uses bracketed root-finding and rejects underround references. A fractional Kelly stake is a sizing choice, not evidence that an estimate is correct.

In tracking, accepted cash stake and entry fees determine exposure. Win and loss settlements move only net profit through the ledger; deposits and withdrawals never count as betting profit. A void can refund its entry fee or retain it, according to the result you record. Linked +EV bets retain the original server probability but recompute predicted EV from the accepted odds and actual fees. Arbitrage legs are logged separately; a single leg is never presented as a completed hedge.

## Data frequency and cost

The Odds API's documented main-market refresh is approximately 60 seconds pregame and 40 seconds in play. Additional markets generally refresh every 60 seconds; exchange refreshes are faster. Polling more rapidly does not remove upstream latency. The first version is for pregame comparison, not subsecond execution.

For 30 continuous days, three populated main markets, and an explicit group of up to ten bookmakers:

| Coverage | Polling | Approximate credits/month |
| --- | --- | ---: |
| Two leagues | 60 seconds | 259,200 |
| Five leagues | 60 seconds | 648,000 |
| Five leagues | 30 seconds | 1,296,000 |

Props typically multiply usage by event and market type. The worker tracks provider quota headers and maintains a separate calendar-month collection budget. Keep a reserve for other usage of the same API key. Provider billing resets may differ from this local calendar-month cap.

References: [pricing](https://the-odds-api.com/), [quota calculation](https://the-odds-api.com/liveapi/guides/v4/), [refresh intervals](https://the-odds-api.com/sports-odds-data/update-intervals.html), [paid request limits](https://the-odds-api.com/guide/rate-limit.html).

## Run with PostgreSQL and Redis locally

With Docker installed:

```sh
# Copy .env.example to .env first if you have not done so.
docker compose up --build
```

Open http://localhost:3000. Compose starts PostgreSQL, Redis, the API, a separate collection worker, and the frontend. Host ports bind to loopback. Database and Redis data use persistent Docker volumes. Stop with `docker compose down`; do not add `-v` unless you intend to delete those volumes.

## Deploy on Vercel / Railway

This repository contains deployment configuration; creating the external services still requires your accounts and environment variables.

1. **Railway:** add PostgreSQL and Redis services.
2. Add an **API service** from this repository. Keep its root at the repository root. The root `railway.json` builds `backend/Dockerfile` and uses `/api/health`.
3. Add a **worker service** from the same repository. Choose `deploy/worker.railway.json` as its configuration file. It uses the same image and starts `python -m evhunter.worker`.
4. Give API and worker the same settings: `APP_ENV=production`, `DATA_MODE`, `DATABASE_URL`, `REDIS_URL`, `OWNER_USERNAME`, a unique `OWNER_PASSWORD`, a random `SESSION_SECRET` of at least 32 characters, and the feed/SMTP settings you need. Set `EMBED_WORKER=false`. Use Railway's service references for database credentials. Give the API a public HTTPS domain.
5. **Vercel:** import this repository, select root directory `apps/web`, use install command `npm ci --prefix ../..`, and build command `npm run build`. Set `BACKEND_URL` to the Railway API's HTTPS origin **before building**. The Next.js rewrite keeps browser requests and cookies on the frontend origin.
6. Set backend `ALLOWED_ORIGINS` to the exact Vercel production origin. Add preview origins explicitly if you use them. Production cookies are Secure and HttpOnly. The default local password and session secret are rejected in production.
7. Verify login, a bankroll adjustment, a manually logged and settled demo bet, source health, and worker freshness before switching to live data.

Database initialization takes a PostgreSQL advisory lock to serialize first boot. Current tables are bootstrapped with SQLAlchemy `create_all`; it does not migrate existing columns. Add versioned schema migrations before changing an existing production schema. Back up PostgreSQL before upgrades. Keep a single API replica and one worker for this MVP; a Redis lease prevents overlapping collection cycles.

## Checks

```sh
npm run typecheck
npm run build
npm audit --omit=dev --audit-level=high
```

```powershell
# Windows
.venv\Scripts\python -m pytest backend/tests -q
```

```sh
# macOS / Linux
.venv/bin/python -m pytest backend/tests -q
```

GitHub Actions also supplies real PostgreSQL and Redis services for an integration test covering concurrent first boot, atomic bankroll reservation, monetary settlement, and login throttling. Local tests skip that integration case unless `TEST_DATABASE_URL` and `TEST_REDIS_URL` point to dedicated test services. The integration test creates and removes an isolated schema.

Tests cover fair-probability normalization, Shin's binary behavior, cent and contract rounding, fees that erase an apparent arbitrage, incomplete/mismatched outcomes, limits, stale data, complementary order books, authentication, CSRF, immutable prediction snapshots, bankroll accounting, idempotent settlement, and demo/live separation.

## Compliance and operating limits

- State laws, age requirements, physical-location rules, allowed sports/props, and operator authorization vary. The jurisdiction setting is not a substitute for reviewing current regulator and venue requirements. New York restricts events involving New York college teams and high-school sports.
- Sports prediction markets face federal/state jurisdiction disputes. Check current access restrictions for Kalshi, Polymarket US, Novig, and ProphetX rather than assuming nationwide eligibility. International Polymarket is kept reference-only in this US MVP.
- Follow sportsbook terms, exchange member agreements, and each market's settlement rules. Rejected stakes, account limits, and differing void decisions can break an intended hedge.
- Data subscriptions do not grant unrestricted redistribution rights. Review rights before offering this terminal to other users, charging for access, publishing feeds, or adding affiliate links.
- No bypass of geographic restrictions, sportsbook scraping, private keys, sportsbook account automation, or automatic bet submission is implemented.
- Calculations are pre-tax and omit funding/FX costs unless entered as actual bet fees. Cash accounting does not model crypto collateral risk. Use only matching supported cash-return contracts.

Primary references: [NY Gaming Commission](https://gaming.ny.gov/sports-wagering), [Kalshi member agreement](https://kalshi.com/docs/kalshi-member-agreement.pdf), [Kalshi fee schedule](https://kalshi.com/fee-schedule), [Polymarket US documentation](https://docs.polymarket.us), [Polymarket geographic restrictions](https://docs.polymarket.com/api-reference/geoblock), [Polymarket fees](https://docs.polymarket.com/trading/fees), [The Odds API terms](https://the-odds-api.com/terms-and-conditions.html).

## Phase 2

Player props and alternate lines; ties/pushes and more complex settlement payoffs; broader sports coverage; source WebSockets and a premium push-feed adapter; deeper-book optimization; closing-line value and probability calibration; automated grading and imports; browser push; portfolio correlation/exposure limits; versioned database migrations; stronger identity-provider authentication and multi-user tenancy.

## Repository layout

```text
apps/web/              Next.js terminal and same-origin API rewrites
backend/evhunter/      API, pricing, providers, worker, database, demo fixtures
backend/tests/         Math, provider, API, and real-service integration checks
backend/requirements.lock
scripts/               Cross-platform local setup and development launcher
deploy/                Worker deployment configuration
compose.yaml           Full local PostgreSQL / Redis stack
.github/workflows/      Automated validation
```
