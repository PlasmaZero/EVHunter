import asyncio
import time
import csv
import hashlib
import hmac
import io
import re
import secrets
from contextlib import asynccontextmanager, suppress
from datetime import timedelta
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text
from starlette.middleware.sessions import SessionMiddleware

from .cache import Cache
from .best_bets import ResearchInput, rank_best_bets, research_key
from .config import Config
from .db import Alert, AuditEvent, Bet, BetResearch, CurrentQuote, History, Ledger, MarketMapping, OpportunityRecord, Preference, SourceState, initialize, make_engine, transaction
from .demo import refresh_demo
from .domain import BOOKS, Quote, as_utc, stable_id, utcnow
from .pricing import decimal_odds, devig, kelly, money
from .services import analyze, bankroll, preferences
from .sports import SPORT_PROFILES, public_profiles
from .worker import run_worker


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Login(Input):
    username: str = Field(max_length=80)
    password: str = Field(max_length=256)


class Preferences(Input):
    enabled_sports: list[str] | None = Field(default=None, max_length=7)
    league_rule_reviews: dict[str, list[str]] | None = None
    min_ev: float = Field(ge=0, le=100)
    min_arb: float = Field(ge=0, le=100)
    devig_method: Literal["proportional", "power", "shin"]
    reference_book: str
    kelly_fraction: float = Field(ge=0, le=1)
    max_stake_pct: float = Field(gt=0, le=100)
    arb_budget: float = Field(gt=0, le=1000000)
    max_quote_age: int = Field(ge=10, le=300)
    max_quote_skew: int = Field(ge=1, le=120)
    jurisdiction: str = Field(min_length=2, max_length=20, pattern=r"^[A-Za-z -]+$")
    allowed_books: list[str] = Field(max_length=11)
    alert_ev: float = Field(ge=0, le=100)
    alert_arb: float = Field(ge=0, le=100)
    email: str = Field(default="", max_length=200)
    email_alerts: bool = False
    venue_balances: dict[str, float] = Field(default_factory=dict)
    jurisdiction_reviewed_at: str | None = None
    confirm_eligibility: bool = False

    @model_validator(mode="after")
    def check(self):
        if self.enabled_sports is not None and (any(s not in SPORT_PROFILES for s in self.enabled_sports) or len(self.enabled_sports) != len(set(self.enabled_sports))):
            raise ValueError("Choose supported leagues without duplicates")
        if self.league_rule_reviews is not None and any(s not in SPORT_PROFILES or any(b not in BOOKS for b in books) or len(books) != len(set(books)) for s, books in self.league_rule_reviews.items()):
            raise ValueError("Choose known leagues and venues for settlement-rule reviews")
        if any(b not in BOOKS or b == "pinnacle" for b in self.allowed_books) or self.reference_book not in BOOKS:
            raise ValueError("Unknown execution venue or reference")
        if "polymarket" in self.allowed_books:
            raise ValueError("International Polymarket is reference-only in this US MVP")
        if self.email and not re.fullmatch(r"[^\s@\r\n]+@[^\s@\r\n]+\.[^\s@\r\n]+", self.email):
            raise ValueError("Provide a valid email address")
        if self.email_alerts and not self.email:
            raise ValueError("Email alerts require a recipient address")
        if any(k not in BOOKS or not 0 <= v <= 100000000 or v != v for k, v in self.venue_balances.items()):
            raise ValueError("Invalid venue balance")
        return self


class Calculation(Input):
    kind: Literal["devig", "kelly", "arb"]
    odds: list[float] = Field(default_factory=list, max_length=20)
    format: Literal["decimal", "american"] = "american"
    method: Literal["proportional", "power", "shin"] = "proportional"
    probability: float = Field(default=.55, gt=0, lt=1)
    bankroll: float = Field(default=10000, ge=0, le=1000000000)
    fraction: float = Field(default=.25, ge=0, le=1)
    budget: float = Field(default=1000, gt=0, le=1000000000)


class BetInput(Input):
    event: str = Field(min_length=1, max_length=180)
    selection: str = Field(min_length=1, max_length=180)
    book: str
    market: str = Field(default="moneyline", max_length=40)
    stake: float = Field(gt=0, le=10000000, multiple_of=0.01)
    odds: float = Field(gt=1, le=10000)
    entry_fee: float = Field(default=0, ge=0, le=1000000, multiple_of=0.01)
    win_fee: float = Field(default=0, ge=0, le=1000000, multiple_of=0.01)
    probability: float | None = Field(default=None, gt=0, lt=1)
    snapshot_id: str | None = Field(default=None, max_length=40)
    notes: str = Field(default="", max_length=1000)


class Settlement(Input):
    status: Literal["won", "lost", "void"]
    refund_entry_fee: bool = True


class CashInput(Input):
    kind: Literal["deposit", "withdrawal"]
    amount: float = Field(gt=0, le=100000000)
    note: str = Field(default="", max_length=200)


class MappingInput(Input):
    market_id: str = Field(pattern=r"^[a-f0-9]{24}$")
    source: Literal["kalshi", "polymarket_us", "polymarket", "novig"]
    external_id: str = Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_-]+$")
    yes_outcome: str = Field(min_length=1, max_length=120)
    token_ids: list[str] = Field(default_factory=list, max_length=2)
    fee_model: Literal["none", "curve", "per_contract"]
    fee_rate: float = Field(ge=0, le=1)
    fee_exponent: float = Field(default=1, ge=1, le=5)
    rules_verified: bool
    fees_reviewed: bool

    @model_validator(mode="after")
    def check(self):
        if not self.rules_verified or not self.fees_reviewed:
            raise ValueError("Review matching settlement rules and the current fee schedule first")
        if self.source == "novig" and (len(self.token_ids) != 2 or len(set(self.token_ids)) != 2 or any(not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", t) for t in self.token_ids)):
            raise ValueError("Two distinct Novig outcome IDs in YES/NO order are required")
        if self.source == "polymarket" and (len(self.token_ids) != 2 or len(set(self.token_ids)) != 2 or any(not t.isdigit() or len(t) > 100 for t in self.token_ids)):
            raise ValueError("Two distinct outcome token IDs are required")
        if self.fee_model == "none" and self.fee_rate != 0:
            raise ValueError("A zero-fee model must have a zero fee rate")
        return self


def require_owner(request: Request):
    if request.session.get("user") != request.app.state.config.owner_username:
        raise HTTPException(401, "Sign in to EVHunter")
    if request.method in {"POST", "PATCH", "PUT", "DELETE"}:
        token = request.headers.get("x-csrf-token", "")
        if not token or not hmac.compare_digest(token, request.session.get("csrf", "")):
            raise HTTPException(403, "Invalid session token. Refresh and retry.")


def lock_owner(session, mode):
    session.execute(select(Preference).where(Preference.mode == mode).with_for_update()).scalar_one()


def bet_view(b):
    return {"id": b.id, "placed_at": as_utc(b.placed_at).isoformat(), "settled_at": as_utc(b.settled_at).isoformat() if b.settled_at else None,
            "stake": b.stake, "odds": b.odds, "status": b.status, "profit": b.profit, **b.data}


def create_app(config: Config | None = None):
    config = config or Config()

    @asynccontextmanager
    async def lifespan(app):
        engine = make_engine(config)
        initialize(engine, config)
        app.state.engine, app.state.cache = engine, Cache(config)
        if config.data_mode == "demo":
            with transaction(engine) as session:
                refresh_demo(session)
        task = asyncio.create_task(run_worker(engine, config, app.state.cache)) if config.embed_worker else None
        yield
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        engine.dispose()

    app = FastAPI(title="EVHunter API", version="0.1.0", lifespan=lifespan,
                  docs_url="/api/docs" if config.app_env != "production" else None,
                  openapi_url="/api/openapi.json" if config.app_env != "production" else None)
    app.state.config = config
    app.add_middleware(SessionMiddleware, secret_key=config.session_secret, session_cookie="evhunter_session",
                       max_age=43200, same_site="lax", https_only=config.app_env == "production")

    @app.middleware("http")
    async def origin_and_headers(request, call_next):
        origin = request.headers.get("origin", "").rstrip("/")
        if request.method in {"POST", "PATCH", "DELETE", "PUT"} and origin and origin not in config.origins:
            return JSONResponse({"detail": "Untrusted request origin"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.get("/api/health")
    def health(request: Request):
        try:
            with request.app.state.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            return {"status": "ok"}
        except Exception:
            raise HTTPException(503, "Database unavailable") from None

    @app.post("/api/auth/login")
    def login(body: Login, request: Request):
        # Use the connection peer, not an untrusted client-supplied X-Forwarded-For header.
        address = request.client.host if request.client else "unknown"
        try:
            allowed = request.app.state.cache.login_allowed(address)
        except Exception:
            raise HTTPException(503, "Sign-in is temporarily unavailable") from None
        if not allowed:
            raise HTTPException(429, "Too many sign-in attempts. Retry in five minutes.")
        def digest(value):
            return hashlib.pbkdf2_hmac("sha256", value.encode(), config.session_secret.encode(), 260000)
        valid = hmac.compare_digest(digest(body.password), digest(config.owner_password))
        if not valid or not hmac.compare_digest(body.username, config.owner_username):
            raise HTTPException(401, "Incorrect username or password")
        request.session.clear()
        request.session.update(user=config.owner_username, csrf=secrets.token_urlsafe(32))
        return {"user": config.owner_username, "csrf": request.session["csrf"], "mode": config.data_mode}

    @app.get("/api/auth/session", dependencies=[Depends(require_owner)])
    def auth_session(request: Request):
        return {"user": config.owner_username, "csrf": request.session["csrf"], "mode": config.data_mode}

    @app.post("/api/auth/logout", dependencies=[Depends(require_owner)])
    def logout(request: Request):
        request.session.clear()
        return {"ok": True}

    @app.get("/api/dashboard", dependencies=[Depends(require_owner)])
    def dashboard(request: Request):
        with transaction(app.state.engine) as session:
            opportunities, counts = analyze(session, config.data_mode, app.state.cache)
            worker = session.get(SourceState, f"{config.data_mode}:worker")
            pref = preferences(session, config.data_mode)
            return {"opportunities": opportunities, "counts": counts, "bankroll": bankroll(session, config.data_mode),
                    "preferences": pref, "books": BOOKS,
                    "sports": public_profiles(),
                    "collection": {"poll_seconds": config.poll_seconds, "estimated_monthly_credits": round(len(pref["enabled_sports"])*3*max(1, (len(config.bookmakers.split(","))+9)//10)*86400*30/config.poll_seconds)},
                    "best_bets": rank_best_bets(session, config.data_mode, opportunities, pref),
                    "mode": config.data_mode, "as_of": utcnow().isoformat(), "streaming": config.data_mode == "live" and bool(config.stream_sources),
                    "worker": worker.data if worker else {"status": "starting"}}

    @app.put("/api/research", dependencies=[Depends(require_owner)])
    def save_research(body: ResearchInput):
        with transaction(app.state.engine) as session:
            lock_owner(session, config.data_mode)
            rows = session.scalars(select(CurrentQuote).where(CurrentQuote.mode == config.data_mode, CurrentQuote.market_id == body.market_id)).all()
            if not rows or body.outcome not in rows[0].data["outcomes"]:
                raise HTTPException(422, "Choose a selection from an ingested market in this workspace")
            profile = SPORT_PROFILES.get(rows[0].data["sport"])
            if not profile or set(body.availability_checks)-set(profile["required_checks"]):
                raise HTTPException(422, "Availability checks must match this market's sport")
            key = f"{config.data_mode}:{research_key(body.market_id, body.outcome)}"
            row = session.get(BetResearch, key)
            now = utcnow()
            payload = {**body.model_dump(mode="json"), "origin": "owner", "updated_at": now.isoformat()}
            if row:
                row.data, row.updated_at = payload, now
            else:
                session.add(BetResearch(id=key, mode=config.data_mode, market_id=body.market_id, updated_at=now, data=payload))
            session.add(AuditEvent(action="research.updated", detail=f"mode={config.data_mode};selection={research_key(body.market_id, body.outcome)}"))
            return payload

    @app.delete("/api/research/{key}", dependencies=[Depends(require_owner)])
    def delete_research(key: str):
        with transaction(app.state.engine) as session:
            lock_owner(session, config.data_mode)
            row = session.get(BetResearch, f"{config.data_mode}:{key}")
            if row:
                session.delete(row)
                session.add(AuditEvent(action="research.deleted", detail=f"mode={config.data_mode};selection={key}"))
            return {"ok": True}

    @app.get("/api/stream", dependencies=[Depends(require_owner)])
    async def live_updates(request: Request):
        async def events():
            pubsub = None
            try:
                if app.state.cache.redis:
                    pubsub = app.state.cache.redis.pubsub(ignore_subscribe_messages=True)
                    await asyncio.to_thread(pubsub.subscribe, f"evhunter:{config.data_mode}:updates")
                yield "event: ready\ndata: connected\n\n"
                started, heartbeat_at = time.monotonic(), time.monotonic()
                while time.monotonic()-started < 25 and not await request.is_disconnected():
                    if pubsub:
                        message = await asyncio.to_thread(pubsub.get_message, timeout=1.0)
                        if message:
                            yield "event: quotes\ndata: updated\n\n"
                    else:
                        await asyncio.sleep(1)
                        yield "event: quotes\ndata: refresh\n\n"
                    if time.monotonic()-heartbeat_at >= 10:
                        yield ": heartbeat\n\n"
                        heartbeat_at = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:
                yield "event: unavailable\ndata: polling fallback\n\n"
            finally:
                if pubsub:
                    with suppress(Exception):
                        await asyncio.to_thread(pubsub.close)
        return StreamingResponse(events(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})

    @app.get("/api/history/{market_id}", dependencies=[Depends(require_owner)])
    def history(market_id: str):
        with transaction(app.state.engine) as session:
            rows = session.scalars(select(History).where(History.mode == config.data_mode, History.market_id == market_id).order_by(History.at.desc()).limit(2000)).all()
            return [{"at": as_utc(r.at).isoformat(), "book": r.book, "outcome": r.outcome, "odds": r.odds} for r in reversed(rows)]

    @app.get("/api/settings", dependencies=[Depends(require_owner)])
    def get_settings():
        with transaction(app.state.engine) as session:
            return preferences(session, config.data_mode)

    @app.put("/api/settings", dependencies=[Depends(require_owner)])
    def set_settings(body: Preferences):
        with transaction(app.state.engine) as session:
            lock_owner(session, config.data_mode)
            prior = preferences(session, config.data_mode)
            if config.data_mode == "live" and (set(body.allowed_books)-set(prior["allowed_books"]) or body.jurisdiction != prior["jurisdiction"]) and not body.confirm_eligibility:
                raise HTTPException(422, "Confirm venue and jurisdiction eligibility before adding execution venues")
            data = body.model_dump(exclude={"confirm_eligibility"})
            for key in ["enabled_sports", "league_rule_reviews"]:
                if data[key] is None:
                    data[key] = prior[key]
            data["jurisdiction_reviewed_at"] = utcnow().isoformat() if body.confirm_eligibility else prior.get("jurisdiction_reviewed_at")
            session.get(Preference, config.data_mode).data = data
            session.add(AuditEvent(action="settings.updated", detail=f"mode={config.data_mode}"))
            return data

    @app.post("/api/calculate", dependencies=[Depends(require_owner)])
    def calculate(body: Calculation):
        try:
            odds = [decimal_odds(o, body.format) for o in body.odds]
            if body.kind == "devig":
                p = devig(odds, body.method)
                return {"probabilities": p, "fair_odds": [1/v for v in p], "overround": (sum(1/o for o in odds)-1)*100, "method": body.method}
            if body.kind == "kelly":
                if len(odds) != 1:
                    raise ValueError("Kelly requires one offered price")
                return kelly(body.probability, odds[0], body.bankroll, body.fraction)
            if len(odds) < 2:
                raise ValueError("Arbitrage requires at least two exhaustive outcomes")
            q = sum(1/o for o in odds)
            stakes = [money(body.budget/(o*q)) for o in odds]
            total = round(sum(stakes), 2)
            profit = money(min(s*o for s, o in zip(stakes, odds))-total)
            return {"implied_total": q, "arb": profit > 0, "stakes": stakes, "total_stake": total, "profit": profit, "roi": profit/total*100 if total else 0}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/bets", dependencies=[Depends(require_owner)])
    def get_bets():
        with transaction(app.state.engine) as session:
            bets = session.scalars(select(Bet).where(Bet.mode == config.data_mode).order_by(Bet.placed_at.desc())).all()
            series, realized, predicted = [], 0.0, 0.0
            for b in sorted([b for b in bets if b.settled_at], key=lambda b: as_utc(b.settled_at)):
                realized += b.profit
                predicted += b.stake*(b.data.get("predicted_ev") or 0)/100
                series.append({"at": as_utc(b.settled_at).isoformat(), "realized": round(realized, 2), "predicted": round(predicted, 2)})
            return {"bets": [bet_view(b) for b in bets], "summary": bankroll(session, config.data_mode), "series": series}

    @app.post("/api/bets", dependencies=[Depends(require_owner)], status_code=201)
    def add_bet(body: BetInput):
        if body.book not in BOOKS or body.book == "pinnacle":
            raise HTTPException(422, "Choose an execution venue")
        with transaction(app.state.engine) as session:
            lock_owner(session, config.data_mode)
            total = round(body.stake+body.entry_fee, 2)
            if total <= 0 or total > bankroll(session, config.data_mode)["available"]:
                raise HTTPException(422, "Stake and entry fee exceed available bankroll")
            if body.win_fee >= body.stake*body.odds:
                raise HTTPException(422, "Win fee must be less than the payout")
            snapshot, p = None, body.probability
            if body.snapshot_id:
                record = session.get(OpportunityRecord, f"{config.data_mode}:{body.snapshot_id}")
                if not record:
                    raise HTTPException(422, "Opportunity snapshot expired; log manually without linking it")
                snapshot = dict(record.data)
                valid_selections = [snapshot["selection"]] if snapshot["type"] == "ev" else [leg["selection"] for leg in snapshot["legs"]]
                if body.event != snapshot["event"] or body.selection not in valid_selections:
                    raise HTTPException(422, "The edited event or selection does not match this snapshot; log it manually")
                if snapshot["type"] == "ev":
                    p = snapshot["true_probability"]
            predicted = (p*(body.stake*body.odds-body.win_fee)/total-1)*100 if p else None
            data = {"event": body.event, "selection": body.selection, "book": body.book, "market": body.market,
                    "wager_stake": body.stake, "entry_fee": body.entry_fee, "win_fee": body.win_fee,
                    "predicted_ev": predicted, "true_probability": p, "notes": body.notes,
                    "snapshot": snapshot, "reference": snapshot.get("reference") if snapshot else None,
                    "method": snapshot.get("method") if snapshot else None, "is_demo": config.data_mode == "demo"}
            bet = Bet(id=secrets.token_hex(12), mode=config.data_mode, placed_at=utcnow(), status="open", stake=total, odds=body.odds, profit=0, data=data)
            session.add(bet)
            session.add(AuditEvent(action="bet.logged", detail=bet.id))
            session.flush()
            return bet_view(bet)

    @app.post("/api/bets/{bet_id}/settle", dependencies=[Depends(require_owner)])
    def settle_bet(bet_id: str, body: Settlement):
        with transaction(app.state.engine) as session:
            lock_owner(session, config.data_mode)
            bet = session.get(Bet, bet_id)
            if not bet or bet.mode != config.data_mode:
                raise HTTPException(404, "Bet not found")
            if bet.status != "open":
                if bet.status == body.status:
                    return bet_view(bet)
                raise HTTPException(409, "This bet is already settled")
            wager = bet.data.get("wager_stake", bet.stake)
            entry, win_fee = bet.data.get("entry_fee", 0), bet.data.get("win_fee", 0)
            profit = money(wager*(bet.odds-1)-entry-win_fee) if body.status == "won" else -bet.stake if body.status == "lost" else 0 if body.refund_entry_fee else -entry
            bet.status, bet.profit, bet.settled_at = body.status, profit, utcnow()
            session.add(Ledger(id=f"settle:{bet.id}", mode=config.data_mode, at=utcnow(), amount=profit, kind="settlement", note=f"{body.status}: {bet.data['selection']}"[:200]))
            session.add(AuditEvent(action="bet.settled", detail=f"{bet.id}:{body.status}"))
            return bet_view(bet)

    @app.get("/api/bets/export", dependencies=[Depends(require_owner)])
    def export_bets():
        with transaction(app.state.engine) as session:
            bets = session.scalars(select(Bet).where(Bet.mode == config.data_mode).order_by(Bet.placed_at.desc())).all()
            output = io.StringIO(newline="")
            writer = csv.writer(output)
            fields = ["placed_at", "event", "selection", "book", "stake", "odds", "status", "profit", "predicted_ev", "is_demo"]
            writer.writerow(fields)
            for bet in bets:
                row = bet_view(bet)
                writer.writerow([("'"+str(row.get(f))) if isinstance(row.get(f), str) and row[f].startswith(("=", "+", "-", "@")) else row.get(f, "") for f in fields])
            return Response(output.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="evhunter-{config.data_mode}-bets.csv"'})

    @app.post("/api/bankroll", dependencies=[Depends(require_owner)], status_code=201)
    def adjust_cash(body: CashInput):
        with transaction(app.state.engine) as session:
            lock_owner(session, config.data_mode)
            amount = round(body.amount, 2)
            if amount <= 0:
                raise HTTPException(422, "Amount must be at least one cent")
            if body.kind == "withdrawal" and amount > bankroll(session, config.data_mode)["available"]:
                raise HTTPException(422, "Withdrawal exceeds available cash")
            session.add(Ledger(id=secrets.token_hex(16), mode=config.data_mode, at=utcnow(), amount=amount if body.kind == "deposit" else -amount, kind=body.kind, note=body.note or body.kind.title()))
            session.flush()
            return bankroll(session, config.data_mode)

    @app.get("/api/sources", dependencies=[Depends(require_owner)])
    def sources():
        with transaction(app.state.engine) as session:
            states = {r.id.split(":", 1)[1]: r.data for r in session.scalars(select(SourceState)).all() if r.id.startswith(config.data_mode+":")}
            rows = session.scalars(select(CurrentQuote).where(CurrentQuote.mode == config.data_mode)).all()
            counts = {b: sum(q.data["book"] == b for q in rows) for b in BOOKS}
            maps = [{"id": m.id, **m.data} for m in session.scalars(select(MarketMapping).where(MarketMapping.mode == config.data_mode)).all()]
            return {"mode": config.data_mode, "states": states, "books": BOOKS, "quote_counts": counts, "mappings": maps,
                    "poll_seconds": config.poll_seconds, "prediction_poll_seconds": config.prediction_poll_seconds,
                    "monthly_credit_budget": config.monthly_credit_budget, "smtp_configured": bool(config.smtp_host),
                    "reviewed_books": config.reviewed_books.split(","), "stream_sources": config.stream_sources}

    @app.get("/api/markets", dependencies=[Depends(require_owner)])
    def markets():
        with transaction(app.state.engine) as session:
            found = {}
            for row in session.scalars(select(CurrentQuote).where(CurrentQuote.mode == config.data_mode)).all():
                q = Quote.model_validate(row.data)
                if q.starts_at > utcnow():
                    found[q.market_id] = {"id": q.market_id, "event": f"{q.away} @ {q.home}", "market": q.market, "line": q.line, "outcomes": q.outcomes, "ruleset": q.ruleset}
            return list(found.values())

    @app.post("/api/mappings", dependencies=[Depends(require_owner)], status_code=201)
    def add_mapping(body: MappingInput):
        with transaction(app.state.engine) as session:
            qrow = session.scalars(select(CurrentQuote).where(CurrentQuote.mode == config.data_mode, CurrentQuote.market_id == body.market_id)).first()
            if not qrow or body.yes_outcome not in qrow.data["outcomes"] or len(qrow.data["outcomes"]) != 2:
                raise HTTPException(422, "Select an existing binary market and its YES outcome")
            identifier = stable_id(config.data_mode, body.source, body.market_id)
            mappings = session.scalars(select(MarketMapping).where(MarketMapping.mode == config.data_mode)).all()
            if len([m for m in mappings if m.data["source"] == body.source]) >= 20 and not session.get(MarketMapping, identifier):
                raise HTTPException(422, "MVP limit: 20 mappings per prediction venue")
            data = {**body.model_dump(), "reviewed_at": utcnow().isoformat()}
            existing = session.get(MarketMapping, identifier)
            if existing:
                existing.data = data
            else:
                session.add(MarketMapping(id=identifier, mode=config.data_mode, data=data))
            return {"id": identifier, **data}

    @app.delete("/api/mappings/{mapping_id}", dependencies=[Depends(require_owner)])
    def remove_mapping(mapping_id: str):
        with transaction(app.state.engine) as session:
            mapping = session.get(MarketMapping, mapping_id)
            if not mapping or mapping.mode != config.data_mode:
                raise HTTPException(404, "Mapping not found")
            # Invalidate linked quotes immediately, before the next source poll.
            source, market = mapping.data["source"], mapping.data["market_id"]
            for row in session.scalars(select(CurrentQuote).where(CurrentQuote.mode == config.data_mode, CurrentQuote.market_id == market)).all():
                if row.data["book"] == source:
                    session.delete(row)
            session.delete(mapping)
            if app.state.cache.redis:
                app.state.cache.redis.delete(f"evhunter:{config.data_mode}:quotes")
            return {"ok": True}

    @app.get("/api/alerts", dependencies=[Depends(require_owner)])
    def alerts():
        with transaction(app.state.engine) as session:
            rows = session.scalars(select(Alert).where(Alert.mode == config.data_mode).order_by(Alert.created_at.desc()).limit(50)).all()
            return [{"id": a.id, "created_at": as_utc(a.created_at).isoformat(), "read": bool(a.read), "delivery": a.delivery, **a.data} for a in rows]

    @app.post("/api/alerts/read", dependencies=[Depends(require_owner)])
    def read_alerts():
        with transaction(app.state.engine) as session:
            for row in session.scalars(select(Alert).where(Alert.mode == config.data_mode, Alert.read == 0)).all():
                row.read = 1
            return {"ok": True}

    return app


app = create_app()
