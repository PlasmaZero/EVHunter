"""Optional read-only exchange streams. Credentials never leave the collection worker."""
import asyncio
import base64
import hashlib
import json
import math
import random
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from sqlalchemy import select
from websockets.asyncio.client import connect

from .config import ROOT
from .db import CurrentQuote, MarketMapping, transaction
from .domain import Quote, as_utc, utcnow
from .providers import make_prediction_quotes, polymarket_us_asks
from .services import analyze, create_threshold_alerts, preferences, save_quotes, source_state

SOURCES = {"kalshi", "polymarket", "polymarket_us", "novig"}


class StreamGap(ValueError):
    """Discard the book and resubscribe; a partial book must never produce a signal."""


def key_from_file(filename):
    path = Path(filename)
    if not path.is_absolute():
        path = ROOT / path
    return serialization.load_pem_private_key(path.read_bytes(), password=None)


def signing_headers(source, config, timestamp=None):
    timestamp = str(timestamp or int(time.time()*1000))
    if source == "polymarket_us":
        raw = base64.b64decode(config.polymarket_us_secret_key, validate=True)
        if len(raw) not in {32, 64}:
            raise ValueError("Expected an Ed25519 secret")
        signature = ed25519.Ed25519PrivateKey.from_private_bytes(raw[:32]).sign(f"{timestamp}GET/v1/ws/markets".encode())
        return {"X-PM-Access-Key": config.polymarket_us_key_id, "X-PM-Timestamp": timestamp, "X-PM-Signature": base64.b64encode(signature).decode()}
    if source == "kalshi":
        key = key_from_file(config.kalshi_private_key_path)
        message = f"{timestamp}GET/trade-api/ws/v2".encode()
        if isinstance(key, ed25519.Ed25519PrivateKey):
            signature = key.sign(message)
        elif isinstance(key, rsa.RSAPrivateKey):
            signature = key.sign(message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())
        else:
            raise ValueError("Unsupported Kalshi key type")
        return {"KALSHI-ACCESS-KEY": config.kalshi_api_key_id, "KALSHI-ACCESS-TIMESTAMP": timestamp, "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode()}
    if source == "novig":
        key = key_from_file(config.novig_private_key_path)
        message = f"NOVIG-V3\n{timestamp}\nGET\n/v3/ws\n\n{hashlib.sha256(b'').hexdigest()}".encode()
        if isinstance(key, ed25519.Ed25519PrivateKey):
            signature = key.sign(message)
        elif isinstance(key, ec.EllipticCurvePrivateKey) and isinstance(key.curve, ec.SECP256R1):
            signature = key.sign(message, ec.ECDSA(hashes.SHA256()))
        else:
            raise ValueError("Unsupported Novig key type")
        return {"Novig-Key-Id": config.novig_key_id, "Novig-Timestamp": timestamp, "Novig-Signature": base64.b64encode(signature).decode()}
    return {}


def credentials_ready(source, config):
    return (source == "polymarket" or
            source == "kalshi" and bool(config.kalshi_api_key_id and config.kalshi_private_key_path) or
            source == "polymarket_us" and bool(config.polymarket_us_key_id and config.polymarket_us_secret_key) or
            source == "novig" and bool(config.novig_key_id and config.novig_private_key_path))


def stamp_from_millis(value):
    result = datetime.fromtimestamp(float(value)/1000, timezone.utc)
    if not math.isfinite(result.timestamp()):
        raise ValueError("Invalid timestamp")
    return result


def levels(rows, cents=False):
    result = {}
    for price, size in rows:
        p, n = float(price)/(100 if cents else 1), float(size)
        if not math.isfinite(p) or not math.isfinite(n) or not 0 < p < 1 or n < 0:
            raise ValueError("Invalid book level")
        if n:
            result[p] = result.get(p, 0)+n
    return result


def opposite_asks(bids):
    return sorted((round(1-p, 8), n) for p, n in bids.items() if n > 0)


@dataclass
class BookUpdate:
    identifier: str
    books: tuple[list, list]
    at: datetime


class KalshiBook:
    def __init__(self):
        self.books = {}
        self.seq = {}

    def process(self, frame):
        kind = frame.get("type")
        if kind == "error":
            raise StreamGap("Kalshi subscription rejected")
        if kind not in {"orderbook_snapshot", "orderbook_delta"}:
            return []
        data, sid, seq = frame["msg"], frame["sid"], int(frame["seq"])
        previous = self.seq.get(sid)
        if previous is not None and seq <= previous:
            return []
        if previous is not None and seq != previous+1:
            raise StreamGap("Kalshi sequence gap")
        self.seq[sid] = seq
        ticker = data["market_ticker"]
        if kind == "orderbook_snapshot":
            self.books[ticker] = {
                "yes": levels(data.get("yes_dollars_fp", data.get("yes_dollars", data.get("yes", []))), cents="yes_dollars_fp" not in data and "yes_dollars" not in data),
                "no": levels(data.get("no_dollars_fp", data.get("no_dollars", data.get("no", []))), cents="no_dollars_fp" not in data and "no_dollars" not in data)}
        else:
            if ticker not in self.books:
                raise StreamGap("Delta arrived before a snapshot")
            side = data["side"]
            if side not in {"yes", "no"}:
                raise StreamGap("Unknown side")
            p = float(data["price_dollars"]) if "price_dollars" in data else float(data["price"])/100
            delta = float(data.get("delta_fp", data.get("delta", 0)))
            if not math.isfinite(p) or not math.isfinite(delta) or not 0 < p < 1:
                raise StreamGap("Invalid delta")
            ladder = self.books[ticker][side]
            amount = ladder.get(p, 0)+delta
            if amount < -1e-8:
                raise StreamGap("Negative liquidity")
            if amount <= 0:
                ladder.pop(p, None)
            else:
                ladder[p] = amount
        millis = frame.get("sending_ts_ms") or data.get("ts_ms")
        if not millis:
            raise StreamGap("Missing source timestamp")
        book = self.books[ticker]
        return [BookUpdate(ticker, (opposite_asks(book["no"]), opposite_asks(book["yes"])), stamp_from_millis(millis))]


class PolymarketBook:
    def __init__(self, mappings):
        self.by_token = {}
        for m in mappings:
            for token in m["token_ids"]:
                self.by_token[token] = m
        self.books, self.stamps = {}, {}

    def process(self, frame):
        if isinstance(frame, list):
            return [u for message in frame for u in self.process(message)]
        kind = frame.get("event_type")
        if kind in {"market_resolved", "market_closed"}:
            market = frame.get("market")
            for token, mapping in self.by_token.items():
                if mapping["external_id"] == market:
                    self.books.pop(token, None)
                    self.stamps.pop(token, None)
            return [BookUpdate(market, ([], []), utcnow())] if market else []
        if kind not in {"book", "price_change"}:
            return []
        at = stamp_from_millis(frame["timestamp"])
        changed = set()
        entries = [frame] if kind == "book" else frame["price_changes"]
        for entry in entries:
            token = entry["asset_id"]
            mapping = self.by_token.get(token)
            if not mapping:
                continue
            if frame.get("market") != mapping["external_id"]:
                raise StreamGap("Outcome token belongs to another condition")
            if token in self.stamps and at < self.stamps[token]:
                continue
            if kind == "book":
                self.books[token] = levels([(r["price"], r["size"]) for r in entry.get("asks", [])])
            elif token not in self.books:
                raise StreamGap("Delta arrived before a snapshot")
            elif entry["side"] == "SELL":
                p, n = float(entry["price"]), float(entry["size"])
                if not math.isfinite(p) or not math.isfinite(n) or not 0 < p < 1 or n < 0:
                    raise StreamGap("Invalid book delta")
                if n:
                    self.books[token][p] = n
                else:
                    self.books[token].pop(p, None)
            self.stamps[token] = at
            changed.add(mapping["external_id"])
        updates = []
        unique = {m["external_id"]: m for m in self.by_token.values()}
        for identifier in changed:
            tokens = unique[identifier]["token_ids"]
            if all(t in self.books for t in tokens):
                updates.append(BookUpdate(identifier, tuple(sorted(self.books[t].items()) for t in tokens), min(self.stamps[t] for t in tokens)))
        return updates


class NovigBook:
    def __init__(self, mappings):
        self.mappings = {m["external_id"]: m for m in mappings}
        self.orders, self.seq, self.open, self.lifecycle_seq = {}, {}, {}, {}

    def process(self, frame):
        if frame.get("error") or frame.get("code"):
            raise StreamGap("Novig subscription rejected")
        snapshot = "snapshot" in frame
        changes = frame.get("snapshot", frame.get("delta", {}))
        updates = []
        for identifier, data in changes.items():
            mapping = self.mappings.get(identifier)
            if not mapping:
                continue
            if "lifecycle" in data:
                lifecycle_seq = int(data["lifecycle"]["seq"])
                if not snapshot and lifecycle_seq != self.lifecycle_seq.get(identifier, -1)+1:
                    raise StreamGap("Novig lifecycle sequence gap")
                self.lifecycle_seq[identifier] = lifecycle_seq
                self.open[identifier] = data["lifecycle"].get("status") == "OPEN"
            if "book" in data:
                book = data["book"]
                seq = int(book["seq"])
                prior = self.seq.get(identifier)
                if not snapshot and (prior is None or seq != prior+1):
                    raise StreamGap("Novig sequence gap")
                self.seq[identifier] = seq
                if snapshot:
                    self.orders[identifier] = {r["order"]: {**r, "outcome": outcome} for outcome, rows in book.get("orders", {}).items() for r in rows}
                else:
                    for delta in book.get("deltas", []):
                        if delta["kind"] == "remove":
                            if delta["order"] not in self.orders[identifier]:
                                raise StreamGap("Unknown removed order")
                            self.orders[identifier].pop(delta["order"])
                        elif delta["kind"] == "add":
                            self.orders[identifier][delta["order"]] = delta
                        else:
                            raise StreamGap("Unknown Novig delta")
            at = stamp_from_millis(frame["ts"])
            if not self.open.get(identifier, False):
                updates.append(BookUpdate(identifier, ([], []), at))
                continue
            ladders = []
            # Resting orders are bids. The opposite outcome supplies executable asks.
            for outcome in reversed(mapping["token_ids"]):
                ladder = levels([(r["price"], r["qty"]) for r in self.orders.get(identifier, {}).values() if r["outcome"] == outcome])
                ladders.append(opposite_asks(ladder))
            updates.append(BookUpdate(identifier, tuple(ladders), at))
        return updates


def set_stream_status(engine, source, status, **extra):
    with transaction(engine) as session:
        source_state(session, "live", f"{source}_stream", status=status, transport="websocket", **extra)


def invalidate(engine, cache, source, market_ids):
    if not market_ids:
        return
    with transaction(engine) as session:
        for row in session.scalars(select(CurrentQuote).where(CurrentQuote.mode == "live", CurrentQuote.market_id.in_(market_ids))).all():
            if row.data["book"] == source and row.data.get("feed") == "direct":
                row.data = {**row.data, "status": "disconnected"}
    with transaction(engine) as session:
        with suppress(Exception):
            cache.publish_quotes(session, "live", 180)


def persist_updates(engine, config, cache, source, targets, pending):
    if not pending:
        return
    now = utcnow()
    with transaction(engine) as session:
        for identifier, update in pending.items():
            for mapping, exemplar in targets.get(identifier, []):
                # Ignore maps removed while a stream callback was queued.
                current = session.get(MarketMapping, mapping["id"])
                if not current or current.data != {k: v for k, v in mapping.items() if k != "id"} or exemplar.starts_at <= now:
                    continue
                rows = session.scalars(select(CurrentQuote).where(CurrentQuote.mode == "live", CurrentQuote.market_id == mapping["market_id"])).all()
                if any(row.data["book"] == source and as_utc(row.data["source_at"]) > update.at for row in rows):
                    continue  # A delayed stream frame cannot roll back a newer REST snapshot.
                for row in rows:
                    if row.data["book"] == source:
                        session.delete(row)
                session.flush()
                quotes = make_prediction_quotes(mapping, exemplar, update.books, update.at)
                save_quotes(session, "live", quotes)
        opportunities, _ = analyze(session, "live", for_alerts=True)
        create_threshold_alerts(session, "live", opportunities, config)
        source_state(session, "live", f"{source}_stream", status="streaming", transport="websocket", last_success=now.isoformat(), market_count=len(targets), message="Live book updates · prices and depth validated")
    with transaction(engine) as session:
        with suppress(Exception):
            cache.publish_quotes(session, "live", 180)


async def heartbeat(ws):
    while True:
        await asyncio.sleep(10)
        await ws.send("PING")


def stream_endpoint(source, config):
    return {"kalshi": "wss://external-api-ws.kalshi.com/trade-api/ws/v2", "polymarket": "wss://ws-subscriptions-clob.polymarket.com/ws/market",
            "polymarket_us": "wss://api.polymarket.us/v1/ws/markets", "novig": "wss://api.novig.com/v3/ws"}[source]


async def stream_source(engine, config, cache, source, targets):
    mappings = [m for pairs in targets.values() for m, _ in pairs]
    market_ids = [m["market_id"] for m in mappings]
    backoff = 1.0
    while True:
        pending = {}
        ping_task = None
        try:
            if source == "kalshi":
                decoder, subscription = KalshiBook(), {"id": 1, "cmd": "subscribe", "params": {"channels": ["orderbook_delta"], "market_tickers": list(targets)}}
            elif source == "polymarket":
                decoder, subscription = PolymarketBook(mappings), {"type": "market", "assets_ids": list({t for m in mappings for t in m["token_ids"]})}
            elif source == "novig":
                decoder, subscription = NovigBook(mappings), {"nonce": 1, "subscribe": {"markets": {identifier: "book" for identifier in targets}}}
            else:
                decoder, subscription = None, {"subscribe": {"requestId": "evhunter-books", "subscriptionType": "SUBSCRIPTION_TYPE_MARKET_DATA", "marketSlugs": list(targets)}}
            headers = signing_headers(source, config)
            async with connect(stream_endpoint(source, config), additional_headers=headers, open_timeout=15, close_timeout=5, ping_interval=15, ping_timeout=20, max_size=2**22, max_queue=64) as ws:
                await ws.send(json.dumps(subscription))
                set_stream_status(engine, source, "waiting_for_data", message="Connected; waiting for a full order-book snapshot")
                if source == "polymarket":
                    ping_task = asyncio.create_task(heartbeat(ws))
                flushed = time.monotonic()
                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=.5)
                        if raw in {"PING", "ping"}:
                            await ws.send("PONG" if raw == "PING" else "pong")
                            continue
                        if raw in {"PONG", "pong"}:
                            continue
                        frame = json.loads(raw)
                        if source == "polymarket_us":
                            if frame.get("error"):
                                raise StreamGap("Polymarket US rejected subscription")
                            data = frame.get("marketData")
                            updates = [BookUpdate(data["marketSlug"], tuple(polymarket_us_asks(frame)), as_utc(data["transactTime"]))] if data else []
                        else:
                            updates = decoder.process(frame)
                        for update in updates:
                            if update.identifier in targets:
                                pending[update.identifier] = update
                    except asyncio.TimeoutError:
                        pass
                    if pending and time.monotonic()-flushed >= .5:
                        persist_updates(engine, config, cache, source, targets, pending)
                        pending.clear()
                        flushed = time.monotonic()
                        backoff = 1
        except asyncio.CancelledError:
            raise
        except Exception:
            # Never print exception URLs, signed headers, key material, or raw frames.
            invalidate(engine, cache, source, market_ids)
            set_stream_status(engine, source, "reconnecting", message="Stream interrupted or invalid book; quotes invalidated and fresh snapshot required")
        finally:
            if ping_task:
                ping_task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await ping_task
        await asyncio.sleep(backoff+random.uniform(0, min(backoff, 3)))
        backoff = min(60, backoff*2)


async def supervise_streams(engine, config, cache):
    tasks, fingerprints, previous_ids = {}, {}, {}
    enabled = set(config.stream_sources)
    try:
        while True:
            grouped = {source: {} for source in SOURCES}
            with transaction(engine) as session:
                examples = {}
                active_sports = set(preferences(session, "live")["enabled_sports"])
                for row in session.scalars(select(CurrentQuote).where(CurrentQuote.mode == "live")).all():
                    if row.data.get("feed", "odds_api") == "odds_api" and row.data["sport"] in active_sports:
                        examples[row.market_id] = Quote.model_validate(row.data)
                maps = session.scalars(select(MarketMapping).where(MarketMapping.mode == "live")).all()
                for row in maps:
                    m = {**row.data, "id": row.id}
                    example = examples.get(m["market_id"])
                    if m["source"] in SOURCES and example and example.starts_at > utcnow():
                        grouped[m["source"]].setdefault(m["external_id"], []).append((m, example))
            for source in SOURCES:
                targets = grouped[source]
                reason = "disabled" if source not in enabled else "needs_mapping" if not targets else "credentials_required" if not credentials_ready(source, config) else None
                fingerprint = json.dumps([(m, q.event_id, q.starts_at.isoformat(), q.ruleset) for pairs in targets.values() for m, q in pairs], sort_keys=True) if reason is None else reason
                if fingerprint == fingerprints.get(source) and source in tasks and not tasks[source].done():
                    continue
                if source in tasks:
                    tasks[source].cancel()
                    with suppress(asyncio.CancelledError, Exception):
                        await tasks.pop(source)
                    invalidate(engine, cache, source, previous_ids.get(source, []))
                fingerprints[source] = fingerprint
                if reason:
                    messages = {"disabled": "Streaming not enabled", "needs_mapping": "Add reviewed mappings to ingested markets first", "credentials_required": "Add the account's API key and signing credential on the server"}
                    set_stream_status(engine, source, reason, message=messages[reason])
                else:
                    previous_ids[source] = [m["market_id"] for pairs in targets.values() for m, _ in pairs]
                    tasks[source] = asyncio.create_task(stream_source(engine, config, cache, source, targets))
            await asyncio.sleep(5)
    finally:
        for task in tasks.values():
            task.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)
        for source, ids in previous_ids.items():
            with suppress(Exception):
                invalidate(engine, cache, source, ids)
