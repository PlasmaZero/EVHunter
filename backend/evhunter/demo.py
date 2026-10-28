"""Synthetic fixtures, isolated from live records. No calls to wagering services."""
import math
from datetime import timedelta

from sqlalchemy import select

from .db import Bet, CurrentQuote, History, Ledger, Preference
from .domain import DEFAULT_EXECUTION_BOOKS, Quote, stable_id, utcnow
from .services import save_quotes, source_state
from .sports import SPORT_PROFILES

GAMES = [
    ("NBA", "Boston Celtics", "New York Knicks", 2.5, .535, -3.5, 224.5),
    ("NBA", "Denver Nuggets", "Oklahoma City Thunder", 4, .465, 2.5, 231.5),
    ("NBA", "Los Angeles Lakers", "Golden State Warriors", 5.5, .555, -4.5, 229.5),
    ("NBA", "Milwaukee Bucks", "Cleveland Cavaliers", 7, .435, 4.5, 222.5),
    ("NFL", "Buffalo Bills", "Kansas City Chiefs", 17, .48, 2.5, 48.5),
    ("NFL", "Philadelphia Eagles", "Dallas Cowboys", 20, .56, -3.5, 45.5),
    ("MLB", "New York Yankees", "Los Angeles Dodgers", 9, .52, -1.5, 8.5),
    ("NHL", "New York Rangers", "New Jersey Devils", 10, .48, 1.5, 6.5),
    ("WNBA", "New York Liberty", "Las Vegas Aces", 11, .54, -3.5, 166.5),
    ("NCAAB", "Duke Blue Devils", "UConn Huskies", 12, .55, -4.5, 145.5),
    ("NCAAF", "Ohio State Buckeyes", "Michigan Wolverines", 13, .57, -6.5, 51.5),
]
BOOK_LIST = list(DEFAULT_EXECUTION_BOOKS)


def refresh_demo(session, seed_history=True):
    session.execute(select(Preference).where(Preference.mode == "demo").with_for_update()).scalar_one()
    now = utcnow()
    existing = session.scalars(select(CurrentQuote).where(CurrentQuote.mode == "demo")).first()
    for row in session.scalars(select(CurrentQuote).where(CurrentQuote.mode == "demo")).all():
        if row.data["book"] not in BOOK_LIST:
            session.delete(row)
    anchor = now.replace(minute=0, second=0, microsecond=0)
    # Keep event times stable across worker refreshes, roll the fixture slate daily.
    day_key = now.strftime("%Y%m%d")
    quotes = []
    for gi, (sport, away, home, hours, home_p, spread, total) in enumerate(GAMES):
        start = anchor.replace(hour=0) + timedelta(hours=hours)
        if start <= now+timedelta(minutes=15):
            start += timedelta(days=1)
        event_id = f"demo-{day_key}-{gi}"
        for mi, kind in enumerate(["moneyline", "spread", "total"]):
            if sport == "NFL" and kind == "moneyline":
                continue
            line = spread if kind == "spread" else total if kind == "total" else None
            outcomes = ["over", "under"] if kind == "total" else [home, away]
            p0 = home_p if kind == "moneyline" else .51 if kind == "spread" else .49
            for oi, outcome in enumerate(outcomes):
                p = p0 if oi == 0 else 1-p0
                label = f"{'Over' if oi == 0 else 'Under'} {line}" if kind == "total" else f"{outcome} {line if oi == 0 else -line:+g}" if kind == "spread" else outcome
                common = dict(event_id=event_id, sport=sport, home=home, away=away, starts_at=start, market=kind, line=line,
                              ruleset=SPORT_PROFILES[sport]["settlement"], outcome=outcome, outcomes=outcomes, selection=label,
                              source_at=now-timedelta(seconds=gi+mi+1), received_at=now, is_demo=True)
                for bi, book in enumerate(BOOK_LIST):
                    # A deterministic slate with good, bad and arbitrage prices; visibly synthetic.
                    edge = -.045 + .006 * ((gi*3 + mi*2 + bi + oi) % 5)
                    if bi == (gi+mi*2+oi*3) % len(BOOK_LIST):
                        edge = .025 + .009*((gi+mi+oi) % 5)
                    if (gi+mi) % 3 == 0 and bi == (oi+gi) % len(BOOK_LIST):
                        edge = .042 + .006*gi
                    d = round(1/(p*1.025) if book == "draftkings" else (1+edge)/p, 4)
                    if book in ["kalshi", "polymarket_us"]:
                        a = round(1/d, 3)
                        quotes.append(Quote(**common, book=book, odds=1/a, share_price=a, fee_model="curve" if book == "kalshi" else "per_contract", fee_rate=.07 if book == "kalshi" else .001,
                                            contracts=500+gi*50, max_stake=600))
                    else:
                        quotes.append(Quote(**common, book=book, odds=d, max_stake=250+bi*50))
    save_quotes(session, "demo", quotes)
    if not existing and seed_history:
        for q in quotes:
            if q.book not in BOOK_LIST:
                continue
            for step in range(24):
                at = now-timedelta(minutes=(24-step)*5)
                wiggle = math.sin(step*.65 + len(q.outcome))*.028 + (24-step)*.0008
                session.add(History(mode="demo", market_id=q.market_id, book=q.book, outcome=q.outcome, at=at, odds=max(1.01, q.odds+wiggle)))
        seed_bets(session, now)
    source_state(session, "demo", "odds_api", status="demo", message="Synthetic FanDuel and DraftKings data", last_success=now.isoformat(), latency_ms=0, credits_used=0)
    for provider in ["kalshi", "polymarket_us", "polymarket"]:
        source_state(session, "demo", provider, status="disabled", message="Direct exchange feeds are available in the live workspace after configuration and mapping")


def seed_bets(session, now):
    if session.get(Bet, "demo-bet-0"):
        return
    for i in range(12):
        stake = [75, 100, 125, 80][i % 4]
        odds = [1.91, 2.10, 1.95, 2.25][i % 4]
        status = "lost" if i in [1, 5, 8, 10] else "won"
        profit = round(stake*(odds-1), 2) if status == "won" else -stake
        at = now-timedelta(days=12-i)
        data = {"event": ["Celtics @ Knicks", "Bills @ Chiefs", "Lakers @ Warriors"][i % 3], "selection": ["Over 224.5", "Chiefs -2.5", "Lakers ML"][i % 3], "book": BOOK_LIST[i % len(BOOK_LIST)], "market": "total" if i % 3 == 0 else "spread" if i % 3 == 1 else "moneyline", "predicted_ev": 2.8+i*.23, "true_probability": .54, "method": "proportional", "reference": "draftkings", "is_demo": True, "notes": "Synthetic settled bet", "fee": 0}
        session.add(Bet(id=f"demo-bet-{i}", mode="demo", placed_at=at, settled_at=at+timedelta(hours=3), status=status, stake=stake, odds=odds, profit=profit, data=data))
        session.add(Ledger(id=f"demo:settle:{i}", mode="demo", at=at+timedelta(hours=3), amount=profit, kind="settlement", note=f"Synthetic settlement {i+1}"))
