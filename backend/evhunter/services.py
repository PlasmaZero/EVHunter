import json
import math
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .db import Alert, Bet, CurrentQuote, History, Ledger, OpportunityRecord, Preference, SourceState
from .domain import BOOKS, Quote, as_utc, stable_id, utcnow
from .pricing import best_arbitrage, devig, effective_odds, fee_for_contracts, kelly, money


def preferences(session, mode):
    return dict(session.get(Preference, mode).data)


def bankroll(session, mode):
    ledger = session.scalars(select(Ledger).where(Ledger.mode == mode)).all()
    bets = session.scalars(select(Bet).where(Bet.mode == mode)).all()
    deposited = round(sum(r.amount for r in ledger if r.kind == "deposit"), 2)
    withdrawn = round(-sum(r.amount for r in ledger if r.kind == "withdrawal"), 2)
    profit = round(sum(b.profit for b in bets if b.status != "open"), 2)
    exposure = round(sum(b.stake for b in bets if b.status == "open"), 2)
    balance = round(sum(r.amount for r in ledger), 2)
    settled_stakes = sum(b.stake for b in bets if b.status != "open")
    return {"bankroll": balance, "available": round(balance - exposure, 2), "exposure": exposure,
            "profit": profit, "deposited": deposited, "withdrawn": withdrawn,
            "roi": profit / settled_stakes * 100 if settled_stakes else 0,
            "predicted_profit": round(sum(b.stake * (b.data.get("predicted_ev") or 0) / 100 for b in bets), 2),
            "bet_count": len(bets), "settled_count": sum(b.status != "open" for b in bets)}


def save_quotes(session, mode: str, quotes: list[Quote], provider: str | None = None, sport: str | None = None):
    if provider and sport:
        # A successful complete snapshot invalidates disappeared selections immediately.
        books = {"kalshi"} if provider == "kalshi" else {provider} if provider.startswith("polymarket") else set(BOOKS) - {"kalshi", "polymarket", "polymarket_us"}
        ids = {f"{mode}:{q.quote_id}" for q in quotes}
        for row in session.scalars(select(CurrentQuote).where(CurrentQuote.mode == mode)).all():
            if row.data["sport"] == sport and row.data["book"] in books and row.id not in ids:
                session.delete(row)
    for q in quotes:
        identifier = f"{mode}:{q.quote_id}"
        row = session.get(CurrentQuote, identifier)
        # Never let an out-of-order source update roll back the current book.
        if row and as_utc(row.data["source_at"]) > q.source_at:
            continue
        history_at = as_utc(row.data.get("_history_at", row.data["source_at"])) if row else q.source_at
        changed = row is None or row.data["odds"] != q.odds or (q.source_at-history_at).total_seconds() >= 600
        payload = {**q.model_dump(mode="json"), "_history_at": (q.source_at if changed else history_at).isoformat()}
        if row:
            row.data = payload
            row.received_at = q.received_at
        else:
            session.add(CurrentQuote(id=identifier, mode=mode, market_id=q.market_id, received_at=q.received_at, data=payload))
        if changed:
            session.add(History(mode=mode, market_id=q.market_id, book=q.book, outcome=q.outcome, at=q.source_at, odds=q.odds))
    session.flush()


def source_state(session, mode, source, **data):
    key = f"{mode}:{source}"
    row = session.get(SourceState, key)
    if row:
        row.data = {**row.data, **data}
    else:
        session.add(SourceState(id=key, data=data))


def load_quotes(session, mode, cache=None):
    if cache:
        try:
            raw = cache.get(f"evhunter:{mode}:quotes")
            if raw:
                return [Quote.model_validate(q) for q in json.loads(raw)]
        except Exception:
            pass  # PostgreSQL remains the source of truth if the cache is unavailable.
    return [Quote.model_validate(r.data) for r in session.scalars(select(CurrentQuote).where(CurrentQuote.mode == mode)).all()]


def quote_view(q: Quote):
    return {"book": q.book, "outcome": q.outcome, "selection": q.selection, "odds": q.odds,
            "effective_odds": effective_odds(q), "source_at": q.source_at.isoformat(),
            "max_stake": q.max_stake, "contracts": q.contracts, "fee_model": q.fee_model}


def analyze(session, mode, cache=None, persist=True, for_alerts=False):
    now = utcnow()
    pref = preferences(session, mode)
    if for_alerts:
        pref["min_ev"] = min(pref["min_ev"], pref["alert_ev"])
        pref["min_arb"] = min(pref["min_arb"], pref["alert_arb"])
    cash = bankroll(session, mode)
    quotes = load_quotes(session, mode, cache)
    groups = defaultdict(list)
    rejected = {"stale": 0, "rules": 0, "fees": 0, "started": 0}
    for q in quotes:
        if q.starts_at <= now:
            rejected["started"] += 1
            continue
        if (now - q.source_at).total_seconds() > pref["max_quote_age"] or (q.source_at - now).total_seconds() > 5:
            rejected["stale"] += 1
            continue
        if not q.rules_verified or q.status != "open":
            rejected["rules"] += 1
            continue
        if q.fee_model == "unknown":
            rejected["fees"] += 1
            continue
        # MVP only supports no-push cash markets. Tie-void NFL moneylines need a third state.
        if (q.market != "moneyline" and (q.line is None or abs(q.line % 1 - 0.5) > 1e-8)) or (q.sport == "NFL" and q.market == "moneyline"):
            rejected["rules"] += 1
            continue
        groups[q.market_id].append(q)
    results = []
    for market_id, rows in groups.items():
        exemplar = rows[0]
        by_book = defaultdict(dict)
        for q in rows:
            by_book[q.book][q.outcome] = q
        base = {"market_id": market_id, "event_id": exemplar.event_id, "event": f"{exemplar.away} @ {exemplar.home}",
                "home": exemplar.home, "away": exemplar.away, "sport": exemplar.sport, "market": exemplar.market,
                "line": exemplar.line, "ruleset": exemplar.ruleset, "starts_at": exemplar.starts_at.isoformat(),
                "observed_at": max(q.received_at for q in rows).isoformat(), "is_demo": mode == "demo",
                "comparison": [quote_view(q) for q in rows]}
        reference = by_book.get(pref["reference_book"], {})
        probabilities = None
        if set(reference) == set(exemplar.outcomes):
            reference_rows = [reference[o] for o in exemplar.outcomes]
            ref_times = [q.source_at.timestamp() for q in reference_rows]
            if max(ref_times) - min(ref_times) <= pref["max_quote_skew"]:
                try:
                    probabilities = dict(zip(exemplar.outcomes, devig([q.odds for q in reference_rows], pref["devig_method"])))
                except ValueError:
                    pass  # A failed devig is never silently replaced by another method.
        eligible = [q for q in rows if q.book in pref["allowed_books"] and q.book != pref["reference_book"]]
        if probabilities:
            for q in eligible:
                # Compare contemporaneous target/reference quotes, not a newly received old line.
                times = [q.source_at.timestamp(), *[r.source_at.timestamp() for r in reference.values()]]
                if max(times) - min(times) > pref["max_quote_skew"]:
                    continue
                p, d = probabilities[q.outcome], effective_odds(q)
                if d <= 1:
                    continue
                sized = kelly(p, d, max(0, cash["available"]), pref["kelly_fraction"], pref["max_stake_pct"])
                stake = min(sized["stake"], q.max_stake if q.max_stake is not None else sized["stake"], pref["venue_balances"].get(q.book, sized["stake"]))
                contracts, fee = None, 0.0
                if q.share_price is not None and stake > 0:
                    contracts = math.floor(stake / (1/d))
                    if q.contracts is not None:
                        contracts = min(contracts, math.floor(q.contracts))
                    while contracts > 0 and contracts*q.share_price + fee_for_contracts(q, contracts) > stake + 1e-8:
                        contracts -= 1
                    if contracts <= 0:
                        continue
                    fee = fee_for_contracts(q, contracts)
                    stake = money(contracts*q.share_price + fee, up=True)
                    d = contracts / stake
                edge = (p*d-1)*100
                if edge < pref["min_ev"]:
                    continue
                results.append({**base, "id": stable_id("ev", market_id, q.book, q.outcome), "type": "ev", "edge": edge,
                                "selection": q.selection, "outcome": q.outcome, "books": [q.book], "odds": q.odds,
                                "effective_odds": d, "true_probability": p, "fair_odds": 1/p,
                                "method": pref["devig_method"], "reference": pref["reference_book"],
                                "source_at": min(as_utc(t) for t in [q.source_at, *[r.source_at for r in reference.values()]]).isoformat(),
                                "stake": money(stake), "profit": money(stake*edge/100), "fee": fee, "contracts": contracts,
                                "size_verified": q.max_stake is not None or q.contracts is not None, "legs": []})
        candidates = {o: [q for q in eligible if q.outcome == o] for o in exemplar.outcomes}
        if all(candidates.values()):
            # Enforce skew on the actual chosen legs by considering only coherent snapshots.
            coherent = {o: [q for q in qs if (max(r.source_at for r in eligible)-q.source_at).total_seconds() <= pref["max_quote_skew"]] for o, qs in candidates.items()}
            if all(coherent.values()):
                arb = best_arbitrage(coherent, min(pref["arb_budget"], max(0, cash["available"])), pref["venue_balances"])
                if arb and arb["edge"] >= pref["min_arb"]:
                    results.append({**base, **arb, "id": stable_id("arb", market_id), "type": "arb", "selection": "All outcomes",
                                    "books": list(dict.fromkeys(l["book"] for l in arb["legs"])), "stake": arb["total_stake"],
                                    "source_at": min(as_utc(l["source_at"]) for l in arb["legs"]).isoformat(),
                                    "odds": None, "true_probability": None, "reference": None, "method": None})
    for op in results:
        op["snapshot_id"] = stable_id(json.dumps(op, sort_keys=True))
    if persist and results:
        insert = postgres_insert if session.get_bind().dialect.name == "postgresql" else sqlite_insert
        statement = insert(OpportunityRecord).values([{"id": f"{mode}:{op['snapshot_id']}", "mode": mode, "data": op, "observed_at": as_utc(op["observed_at"])} for op in results])
        session.execute(statement.on_conflict_do_nothing(index_elements=["id"]))
    return sorted(results, key=lambda o: o["edge"], reverse=True), {"quote_count": len(quotes), "market_count": len(groups), "event_count": len({q.event_id for q in quotes if q.starts_at > now}), "rejected": rejected}


def create_threshold_alerts(session, mode, opportunities, config):
    pref = preferences(session, mode)
    key = f"{mode}:threshold_state"
    state = session.get(SourceState, key)
    previous = set(state.data.get("above", [])) if state else set()
    above = {o["id"] for o in opportunities if o["edge"] >= (pref["alert_ev"] if o["type"] == "ev" else pref["alert_arb"])}
    now = utcnow()
    recent = session.scalars(select(Alert).where(Alert.mode == mode, Alert.created_at > now-timedelta(minutes=10))).all()
    recent_ids = {a.data.get("opportunity_id") for a in recent}
    for op in opportunities:
        if op["id"] not in above - previous or op["id"] in recent_ids:
            continue
        alert = {"opportunity_id": op["id"], "snapshot_id": op["snapshot_id"], "event": op["event"], "selection": op["selection"], "edge": op["edge"], "type": op["type"], "source_at": op["source_at"], "is_demo": mode == "demo", "attempts": 0}
        session.add(Alert(id=stable_id(mode, op["id"], now.isoformat()), mode=mode, created_at=now, data=alert,
                          delivery="pending" if pref["email_alerts"] and pref["email"] and config.smtp_host and mode == "live" else "in_app"))
    source_state(session, mode, "threshold_state", above=sorted(above))


def prune(session, mode, retention_days):
    cutoff = utcnow() - timedelta(days=retention_days)
    session.execute(delete(History).where(History.mode == mode, History.at < cutoff))
    session.execute(delete(OpportunityRecord).where(OpportunityRecord.mode == mode, OpportunityRecord.observed_at < cutoff))
    session.execute(delete(CurrentQuote).where(CurrentQuote.mode == mode, CurrentQuote.received_at < utcnow()-timedelta(days=2)))
