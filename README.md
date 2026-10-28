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

The default sportsbook feed and execution venues are **FanDuel and DraftKings**. DraftKings is the initial reference for estimating +EV at FanDuel; it is still included in arbitrage comparisons. This uses a retail pricing proxy, so it does not establish an independently validated probability. Change the reference in Settings when you have another supported benchmark.

For an existing checkout, run `git pull` and restart. The feed removes Fanatics from old `BOOKMAKERS` values. A one-time migration removes Fanatics from saved execution and rule-review lists, preserving other venues, strategy settings, bets, and bankroll records. A Fanatics reference is changed to DraftKings.

Get an API key from [The Odds API](https://the-odds-api.com/). Its paid coverage currently lists FanDuel, DraftKings, BetMGM, Fanatics, Caesars, Novig, ProphetX, and Pinnacle. Coverage varies by sport and market. API access and data-use rights are governed by the provider agreement.

In your **untracked** `.env`:

```dotenv
DATA_MODE=live
ODDS_API_KEY=your-real-key
BOOKMAKERS=fanduel,draftkings
POLL_SECONDS=60
SPORTS=basketball_nba,americanfootball_nfl,baseball_mlb,icehockey_nhl
```

Choose monitored leagues in **Settings → Sports & leagues**, then review matching settlement rules for each league and venue. `SPORTS` initializes the league selection once; saved Settings take precedence after that. Existing selections are preserved. New installations initially monitor NBA, NFL, MLB, and NHL. WNBA, NCAAB, and NCAAF can also be enabled.

For the legacy NBA/NFL setup, this environment setting initializes the rule review:

```dotenv
REVIEWED_BOOKS=fanduel,draftkings
```

Unreviewed feed quotes remain visible in coverage counts but cannot enter opportunity calculations. Adding MLB, NHL, WNBA, or college leagues does not inherit NBA/NFL settlement reviews. Review each new league/venue in Settings. MLB requires attention to action/listed-pitcher, extra-innings, shortened-game, and void rules; NHL requires matching overtime/shootout treatment. The app records your review rather than verifying operator rules automatically. Fanatics has been removed from the active feed and default selectors. Other adapters remain available for a future expansion but are not part of the default feed. If you later add Pinnacle as a reference, its aggregated odds may be delayed.

**Novig and ProphetX are excluded from live calculations until their fee treatment is configured.** Do not assume either is fee-free. `EXCHANGE_FEE_CONFIG` accepts reviewed profit-commission or verified zero-fee sportsbook-style returns, for example:

```dotenv
# Example structure only: supply the current verified rate and actual review date.
EXCHANGE_FEE_CONFIG={"novig":{"model":"profit","rate":0.02,"reviewed_at":"YYYY-MM-DD"}}
```

The sample rate is not a statement about Novig's current fee schedule. Unsupported fee models must remain excluded. Prediction-contract fee schedules should use a direct, reviewed mapping instead.

Restart the app after changing environment variables. Live mode starts with **zero tracked cash**. Use **Bet tracker → Deposit / withdrawal** to record your bankroll; this only updates your ledger. +EV prices can be displayed without cash, but sizing is zero and arbitrage allocations require available bankroll.

### Check the connection before looking for opportunities

Open **Data sources**. Each card now reports whether credentials are missing, malformed, configured locally, or unnecessary; whether its stream is enabled; and how many reviewed contract mappings exist. No API keys, key IDs, signatures, or private-key paths are returned to the browser.

- Keep `DATA_MODE=demo` for synthetic data. Set `DATA_MODE=live` and restart the API and worker when ready to use real data. Adding an odds key alone does not change workspaces.
- `BOOKMAKERS=fanduel,draftkings` uses the free-compatible sportsbook selection. The default local calendar-month budget is **450 credits**, leaving a reserve against the provider's 500-credit free allowance. Set `MONTHLY_CREDIT_BUDGET` to fit your actual plan. An existing `.env` keeps its prior budget, so check that value on upgrade.
- Continuous polling uses more than a free allowance. Limit monitored leagues and collection time; the Sources page displays the usage estimate, remaining provider credits when reported, and the reason collection is paused.
- Bad keys, exhausted provider credits, rate limits, and successful responses with no supported odds have separate statuses. **Retry sportsbook feed** clears its cooldown after correcting setup; it does not override budget limits.
- `PREDICTION_STREAMS=auto` (also the meaning of an old blank value) enables streams with complete credential pairs and public international Polymarket data. Connections open only after a reviewed mapping exists. Use `off` to disable streams, or an explicit venue list.
- Kalshi needs its API key ID plus an RSA or Ed25519 signing PEM. Novig needs a `trading::read` key ID plus its Ed25519/P-256 signing PEM; a management-only key cannot open this stream. Mount the PEM and set its `_PRIVATE_KEY_PATH`, or use `_PRIVATE_KEY` for an inline PEM. Literal escaped newlines are accepted. Never commit keys.
- **Polymarket US** uses `POLYMARKET_US_KEY_ID` and `POLYMARKET_US_SECRET_KEY`, a base64 Ed25519 secret. The official SDK names `POLYMARKET_KEY_ID` and `POLYMARKET_SECRET_KEY` are also accepted. International CLOB API key/secret/passphrase credentials are different and must not be placed in the US fields. International public market data needs no trading credentials.

Credentials do not discover or approve matching contracts. Enable any additional execution venues in Settings, then add reviewed mappings below. Sportsbook odds must be ingested first so the exact event, line, outcomes, and settlement identity can be selected.

### 2. Map Kalshi and Polymarket contracts

Direct integrations use public read endpoints:

| Venue | Adapter | Execution treatment |
| --- | --- | --- |
| Kalshi | REST market metadata and order-book snapshots; complementary YES/NO bids become executable asks | Requires a reviewed matching contract, fee model, and venue eligibility |
| Polymarket US | Separate public gateway market books; offers and complementary bids | Requires a reviewed matching US contract and fee model |
| Polymarket international | CLOB order books for two explicitly supplied outcome token IDs | Reference-only in this US-focused MVP |

After sportsbook markets are ingested, open **Data sources → Map a market**. Select the canonical event and exact line, identify the external contract, map its YES outcome, enter the reviewed fee model, and confirm matching rules. No fuzzy name match can silently authorize a cross-venue arbitrage.

For international Polymarket, enter the condition ID and the YES/NO token IDs. The adapter verifies the order books' condition IDs when supplied. For US books, a suspended or non-open state is excluded. Kalshi snapshots must refer to an active market.

Direct REST adapters poll at 20 seconds by default; optional direct streams are described below. They intentionally use top-of-book available size and whole-contract quantities. When a venue does not provide a source timestamp, the quote explicitly records a snapshot-receipt timestamp basis. Those timestamps are not represented as venue-originated updates.

The MVP limits each prediction venue to 20 reviewed mappings. Higher-throughput discovery and deeper-book optimization are phase-2 work.

### 3. Set your strategy

In **Settings**, select permitted execution venues, jurisdiction, reference source, devig method, minimum edges, Kelly fraction, stake caps, freshness limits, and alerts. Live additions to execution venues require an explicit eligibility review. This is your recorded review, not a geolocation service or legal determination.

A complete reference outcome set is required. If its price is missing, stale, or cannot be devigged, the engine emits no +EV signal for that reference. It never silently switches devig methods.

### 4. Email alerts

Set the SMTP variables in `.env`, then enable email alerts and enter your own recipient address in Settings. SMTP credentials are never returned to the browser. SMTP delivery is disabled in demo mode. Alerts are queued, deduplicated for ten minutes, retried with bounded backoff, and discarded when their underlying quotes expire.

## Optional direct real-time feeds

Direct WebSocket adapters are implemented for **Kalshi, Polymarket US, Polymarket international, and Novig**. The three sportsbook defaults stay unchanged. Streaming is disabled until you opt in and add reviewed market mappings.

After pulling updates, run `npm run setup` to install the signing dependency, then configure only the sources you want:

```dotenv
PREDICTION_STREAMS=auto
KALSHI_API_KEY_ID=
KALSHI_PRIVATE_KEY_PATH=secrets/kalshi.pem
# Or KALSHI_PRIVATE_KEY with a server-side inline PEM.
POLYMARKET_US_KEY_ID=
POLYMARKET_US_SECRET_KEY=
NOVIG_KEY_ID=
NOVIG_PRIVATE_KEY_PATH=secrets/novig.pem
# Or NOVIG_PRIVATE_KEY with a server-side inline PEM.
```

Use local paths to downloaded API signing keys, kept outside the repository. Polymarket US uses its API secret key in base64. These are service API signing credentials, never a wallet seed phrase. Keys remain on the worker and are never returned to the browser.

- **Kalshi:** public REST reads require no key; its WebSocket handshake requires an account API key and an RSA or Ed25519 signing key.
- **Polymarket US:** public REST reads need no key; its market WebSocket requires account API credentials.
- **Polymarket international:** public market WebSockets use no account key. Add `polymarket` to `PREDICTION_STREAMS` to stream reviewed outcome tokens. It stays reference-only in this US-focused app.
- **Novig:** the direct V3 WebSocket requires an eligible production account and a trading read key (`trading::read`). Create and register that key through Novig's documented account setup. The connector only sends market-data subscriptions. It does not open or fund subaccounts, place orders, or bypass device/location checks. Paper keys are not accepted by this production connector.

In **Data sources → Map a market**, link the contract to an existing ingested binary market. Novig requires its market ID and the two outcome IDs in YES/NO order. Review settlement and fees before saving. The initial linking workflow still starts from ingested sportsbook events; standalone prediction-only discovery is not implemented.

The worker maintains books from snapshots and deltas, reconnects with backoff, rejects sequence gaps, and invalidates disconnected books. Quotes and alerts are batched at most twice per second. The browser receives server-sent update notifications; its five-second polling remains a fallback. Status cards distinguish polling, active streams, missing credentials, and reconnects.

Novig's V3 contracts pay **$0.01**, compared with **$1** for the supported Kalshi/Polymarket binary contracts. The engine scales returns, depth, and curve fees to the actual contract payout. Fees round conservatively to cents, which can exclude very small trades. Do not copy a $1-contract fee or share calculation onto Novig.

Data-access costs are separate from trading fees, infrastructure bills, and data-use rights. Kalshi/Polymarket expose public reads; authenticated streaming needs eligible accounts. Novig's documentation describes authenticated access and throttling rather than a guaranteed universally free data license. Verify its account and commercial-use terms.

References: [Kalshi streams](https://docs.kalshi.com/getting_started/quick_start_websockets), [Polymarket US streams](https://docs.polymarket.us/api-reference/websocket/markets), [Polymarket market stream](https://docs.polymarket.com/market-data/realtime-data), [Novig API](https://docs.novig.com/), [Novig stream](https://docs.novig.com/api/streaming/connection), [Novig fees and penny payouts](https://docs.novig.com/api/concepts/fees).

## Best Bets research grades

Open **Best bets** in the sidebar to rank detected +EV selections by a transparent 0–100 research grade. Select a row to inspect every factor, source date, uncertainty, and grade cap; log an accepted bet through the existing tracker.

| Factor | Maximum points |
| --- | ---: |
| Fee-adjusted estimated EV | 35 |
| Matchup and scoring baseline | 20 |
| Injury report / IR | 15 |
| Reference quality and cross-book confirmation | 15 |
| Quote freshness and reported execution size | 15 |

The grade is a heuristic research-priority score, **not a win probability or a backtested prediction model**. EV, probability, and capped Kelly stakes retain their existing calculations. Cross-book agreement does not establish independent model accuracy. Retail references receive less benchmark credit than sharp/exchange references.

**Edit research** saves evidence against the exact canonical market, line, and outcome, shared across offered books. Add both teams' scoring averages and sample sizes, sport-specific matchup reasoning, and an injury/availability assessment. NBA/WNBA/NCAAB use points, pace, usage, positional defense and rotations; MLB uses runs, starting pitchers, handedness, lineups, bullpen workload and park/weather context; NHL uses goals, goalies, expected goals, special teams and line combinations; NFL/NCAAF use coverage, WR/CB alignment, line play and workload. Each reviewed block requires its source URL and actual as-of time. Sources and assessments are owner-supplied; the application does not fetch or independently verify injury reports or source-page contents. No additional API or AI key is required for this version. Existing odds feeds do not supply this research.

The scoring baseline equally blends scoring with the opponent's scoring allowed, using the sport's units. Its direction is evaluated against the exact spread, run line, puck line or total, without converting it into a win probability. The directional sensitivity scale is a sport-specific rubric setting, not a fitted prediction-error estimate.

| League | Units | Minimum games/team | Statistics expiry | Matchup expiry | Availability expiry |
| --- | --- | ---: | ---: | ---: | ---: |
| NBA / WNBA / NCAAB | Points | 4 | 72h | 24h | 6h |
| NFL / NCAAF | Points | 4 | 168h | 24h | 6h |
| MLB | Runs | 10 | 72h | 12h | 3h |
| NHL | Goals | 6 | 72h | 12h | 3h |

MLB reviews also require explicit confirmation of both starting pitchers, both batting orders, and bullpen availability. NHL reviews require confirmed starting goalies and reviewed lines/scratches. Unconfirmed availability remains provisional even if the injury assessment says clear. Missing/stale/small-sample research caps the grade at 69; material injury risk caps it at 59; unresolved injury risk caps it at 49. A candidate needs a grade of at least 70, current research, no material or unresolved injury risk, and positive funded sizing. Labels are A ≥85, B ≥70, C ≥55, D <55.

Demo mode includes clearly labeled fictional research examples. Live mode never inherits those examples. The new `bet_research` table is additive and is created by the existing initialization path; no existing tables or bankroll data are replaced. Research writes require the owner session and CSRF token and are recorded in the audit log. Player props across sports still require a future ingestion extension; this section grades the current moneyline/spread/totals feed.

## Pricing and matching rules

Canonical identity includes event, market type, exact home spread or total line, complete outcome set, and settlement identity. Supported leagues are NBA, NFL, MLB, NHL, WNBA, NCAAB, and NCAAF. The adapters collect full-game two-outcome moneylines, spreads/run lines/puck lines, and totals. Comparisons require reviewed matching settlement rules and half-unit handicaps/totals. NFL tie-void moneylines, whole-number lines, three-way/draw markets, player props, and ambiguous settlement cases remain excluded. MLB/NHL settlement identities distinguish extra innings and overtime/shootouts from the basketball/football conventions. This is league coverage, not support for every market offered in each sport.

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
| Four leagues | 60 seconds | 518,400 |
| Seven leagues | 60 seconds | 907,200 |
| Five leagues | 30 seconds | 1,296,000 |

Choose leagues in Settings; pausing a league stops its sportsbook polling and mapped streams, and excludes its cached quotes from analysis. Adding leagues increases credit use. Coverage depends on provider season/market availability; off-season or empty responses may reduce usage. [Supported provider sport keys](https://the-odds-api.com/sports-odds-data/sports-apis.html).

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

Player props and alternate lines; ties/pushes and more complex settlement payoffs; broader sports coverage; a premium sportsbook push-feed adapter; deeper-book optimization; closing-line value and probability calibration; automated grading and imports; browser push; portfolio correlation/exposure limits; versioned database migrations; stronger identity-provider authentication and multi-user tenancy.

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
