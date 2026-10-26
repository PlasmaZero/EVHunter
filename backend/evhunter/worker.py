import asyncio
import json
import logging
import secrets
import smtplib
import ssl
import time
from contextlib import suppress
from email.message import EmailMessage

import httpx
from sqlalchemy import select

from .cache import Cache
from .config import Config
from .db import Alert, CurrentQuote, MarketMapping, SourceState, initialize, make_engine, transaction
from .demo import refresh_demo
from .domain import Quote, as_utc, utcnow
from .providers import ProviderError, SPORTS, collect_prediction, collect_sportsbook
from .services import analyze, create_threshold_alerts, preferences, prune, save_quotes, source_state
from .streams import supervise_streams

log = logging.getLogger("evhunter.worker")


def send_email(config, recipient, alert):
    msg = EmailMessage()
    msg["From"] = config.smtp_from
    msg["To"] = recipient
    msg["Subject"] = f"EVHunter: {alert['edge']:.2f}% {'+EV' if alert['type']=='ev' else 'arb'} · {alert['event']}"
    msg.set_content(f"{alert['event']}\n{alert['selection']}\nEstimated edge: {alert['edge']:.2f}%\nObserved: {alert['source_at']}\nRecheck prices, availability, and settlement before acting.")
    constructor = smtplib.SMTP_SSL if config.smtp_ssl else smtplib.SMTP
    with constructor(config.smtp_host, config.smtp_port, timeout=10) as smtp:
        if config.smtp_starttls and not config.smtp_ssl:
            smtp.starttls(context=ssl.create_default_context())
        if config.smtp_username:
            smtp.login(config.smtp_username, config.smtp_password)
        smtp.send_message(msg)


async def deliver_alerts(engine, config):
    if config.data_mode != "live" or not config.smtp_host:
        return
    with transaction(engine) as session:
        pref = preferences(session, "live")
        pending = session.scalars(select(Alert).where(Alert.mode == "live", Alert.delivery == "pending").limit(10)).all()
        for alert in pending:
            data = dict(alert.data)
            # Do not deliver a delayed alert once its source quotes have expired.
            if (utcnow()-as_utc(data["source_at"])).total_seconds() > pref["max_quote_age"]:
                alert.delivery = "expired"
                continue
            if not pref["email_alerts"] or not pref["email"]:
                alert.delivery = "in_app"
                continue
            if data.get("retry_at", 0) > time.time():
                continue
            try:
                await asyncio.to_thread(send_email, config, pref["email"], data)
                alert.delivery = "sent"
            except (OSError, smtplib.SMTPException):
                data["attempts"] = data.get("attempts", 0)+1
                data["retry_at"] = time.time()+min(300, 15*2**data["attempts"])
                alert.data = data
                if data["attempts"] >= 4:
                    alert.delivery = "failed"


async def tick(engine, config, cache):
    mode = config.data_mode
    async with httpx.AsyncClient(timeout=10, limits=httpx.Limits(max_connections=4)) as client:
        with transaction(engine) as session:
            if mode == "demo":
                refresh_demo(session)
            else:
                state = session.get(SourceState, "live:odds_api")
                old = dict(state.data) if state else {}
                now = utcnow()
                last = as_utc(old["last_attempt"]) if old.get("last_attempt") else None
                month = now.strftime("%Y-%m")
                spent = old.get("budget_spent", 0) if old.get("budget_month") == month else 0
                if not config.odds_api_key:
                    source_state(session, mode, "odds_api", status="missing_key", message="Add ODDS_API_KEY on the server to enable sportsbook data")
                elif not last or (now-last).total_seconds() >= config.poll_seconds:
                    if spent >= config.monthly_credit_budget:
                        source_state(session, mode, "odds_api", status="budget_paused", message="Calendar-month credit budget reached")
                    else:
                        started = time.monotonic()
                        try:
                            rejected = 0
                            usage = {}
                            for key in config.sports.split(","):
                                if key not in SPORTS:
                                    continue
                                if spent >= config.monthly_credit_budget:
                                    break
                                quotes, invalid, usage = await collect_sportsbook(client, config, key)
                                save_quotes(session, mode, quotes, provider="odds_api", sport=SPORTS[key])
                                rejected += invalid
                                spent += usage["last_cost"]
                                await asyncio.sleep(.1)
                            source_state(session, mode, "odds_api", status="connected", message=f"Pregame feed · {rejected} invalid selections excluded",
                                         last_attempt=now.isoformat(), last_success=now.isoformat(), latency_ms=round((time.monotonic()-started)*1000),
                                         budget_spent=spent, budget_month=month, **usage)
                        except ProviderError as exc:
                            source_state(session, mode, "odds_api", status="error", message=str(exc), last_attempt=now.isoformat(), budget_spent=spent, budget_month=month)
                mappings = session.scalars(select(MarketMapping).where(MarketMapping.mode == mode)).all()
                exemplars = {q.market_id: Quote.model_validate(q.data) for q in session.scalars(select(CurrentQuote).where(CurrentQuote.mode == mode)).all() if q.data["book"] not in ["kalshi", "polymarket", "polymarket_us"]}
                for source in ["kalshi", "polymarket_us", "polymarket", "novig"]:
                    maps = [m.data for m in mappings if m.data["source"] == source]
                    if not maps:
                        source_state(session, mode, source, status="needs_mapping", message="Add a reviewed market mapping to collect quotes")
                        continue
                    if source == "novig":
                        source_state(session, mode, source, status="stream_only", message="Direct Novig data uses the optional authenticated stream")
                        continue
                    prior = session.get(SourceState, f"{mode}:{source}")
                    if prior and prior.data.get("last_attempt") and (now-as_utc(prior.data["last_attempt"])).total_seconds() < config.prediction_poll_seconds:
                        continue
                    collected, errors = [], []
                    for mapping in maps[:20]:
                        example = exemplars.get(mapping["market_id"])
                        if not example or example.starts_at <= now:
                            continue
                        try:
                            collected.extend(await collect_prediction(client, config, mapping, example))
                        except (ProviderError, KeyError, ValueError, TypeError):
                            errors.append(mapping["external_id"])
                        await asyncio.sleep(.12)
                    # Preserve failed mappings only until TTL, never refresh their source timestamps.
                    if not errors:
                        for sport in SPORTS.values():
                            save_quotes(session, mode, [q for q in collected if q.sport == sport], provider=source, sport=sport)
                    else:
                        save_quotes(session, mode, collected)
                    source_state(session, mode, source, status="error" if errors else "connected", message=f"{len(collected)} quotes · {len(errors)} mappings unavailable",
                                 last_attempt=now.isoformat(), **({"last_success": now.isoformat()} if not errors else {}))
            opportunities, counts = analyze(session, mode, for_alerts=True)
            create_threshold_alerts(session, mode, opportunities, config)
            source_state(session, mode, "worker", status="running", last_success=utcnow().isoformat(), **counts)
            prune(session, mode, config.history_retention_days)
        # Cache only committed state. TTL never changes source timestamps used by the engine.
        with transaction(engine) as session:
            with suppress(Exception):
                cache.publish_quotes(session, mode, 180)
        await deliver_alerts(engine, config)


async def poll_loop(engine, config, cache):
    while True:
        try:
            await asyncio.wait_for(tick(engine, config, cache), timeout=240)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("Collection cycle failed; existing quotes will expire normally")
        await asyncio.sleep(20 if config.data_mode == "demo" else 5)


async def maintain_lease(cache, key, token):
    while True:
        await asyncio.sleep(30)
        renewed = cache.redis.eval("if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('EXPIRE',KEYS[1],300) else return 0 end", 1, key, token)
        if not renewed:
            raise RuntimeError("Worker lease lost")


async def run_worker(engine, config, cache):
    # One leader owns both persistent sockets and periodic polling.
    while True:
        token, locked, tasks = secrets.token_hex(16), False, []
        lock_key = f"evhunter:{config.data_mode}:worker_lock"
        try:
            if cache.redis:
                locked = bool(cache.redis.set(lock_key, token, nx=True, ex=300))
                if not locked:
                    await asyncio.sleep(5)
                    continue
            tasks.append(asyncio.create_task(poll_loop(engine, config, cache)))
            if config.data_mode == "live":
                tasks.append(asyncio.create_task(supervise_streams(engine, config, cache)))
            if cache.redis:
                tasks.append(asyncio.create_task(maintain_lease(cache, lock_key, token)))
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.error("Worker leadership interrupted; reconnecting without using partial books")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if locked:
                with suppress(Exception):
                    cache.redis.eval("if redis.call('GET',KEYS[1])==ARGV[1] then return redis.call('DEL',KEYS[1]) else return 0 end", 1, lock_key, token)
        await asyncio.sleep(5)


def main():
    logging.basicConfig(level=logging.INFO)
    config = Config()
    engine = make_engine(config)
    initialize(engine, config)
    asyncio.run(run_worker(engine, config, Cache(config)))


if __name__ == "__main__":
    main()
