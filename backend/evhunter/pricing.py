"""Pure pricing functions. All monetary rounding is explicit and conservative."""
import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from itertools import product

from .domain import Quote


def money(value: float, up: bool = False) -> float:
    # Remove sub-nanodollar binary-float artifacts before conservative cent rounding.
    exact = Decimal(str(value)) + (Decimal("-0.000000001") if up else Decimal("0.000000001"))
    return float(exact.quantize(Decimal("0.01"), rounding=ROUND_CEILING if up else ROUND_FLOOR))


def decimal_odds(value: float, format: str = "decimal") -> float:
    if not math.isfinite(value):
        raise ValueError("Odds must be finite")
    if format == "american":
        if abs(value) < 100:
            raise ValueError("American odds must be at least +100 or at most -100")
        return 1 + (value / 100 if value > 0 else 100 / -value)
    if format != "decimal" or value <= 1:
        raise ValueError("Decimal odds must be greater than 1")
    return value


def american_odds(decimal: float) -> int:
    return round((decimal - 1) * 100) if decimal >= 2 else round(-100 / (decimal - 1))


def devig(odds: list[float], method: str = "proportional") -> list[float]:
    if len(odds) < 2 or len(odds) > 20:
        raise ValueError("Provide 2 to 20 exhaustive outcomes")
    q = [1 / decimal_odds(d) for d in odds]
    total = sum(q)
    if method == "proportional":
        return [v / total for v in q]
    if method == "power":
        lo, hi = 0.000001, 1000.0
        for _ in range(100):
            k = (lo + hi) / 2
            if sum(v ** k for v in q) > 1:
                lo = k
            else:
                hi = k
        p = [v ** ((lo + hi) / 2) for v in q]
    elif method == "shin":
        if total < 1 - 1e-10:
            raise ValueError("Shin requires an overround reference, not an underround")
        if abs(total - 1) < 1e-10:
            return q
        # Shin (1992): solve the insider proportion z, with bracketed bisection.
        def probabilities(z):
            return [(math.sqrt(z*z + 4*(1-z)*v*v/total) - z) / (2*(1-z)) for v in q]
        lo, hi = 0.0, 0.999999
        for _ in range(100):
            z = (lo + hi) / 2
            if sum(probabilities(z)) > 1:
                lo = z
            else:
                hi = z
        p = probabilities((lo + hi) / 2)
        if any(v <= 0 for v in p) or abs(sum(p) - 1) > 1e-7:
            raise ValueError("Shin did not converge for this reference")
    else:
        raise ValueError("Unknown devig method")
    if abs(sum(p) - 1) > 1e-7 or any(not math.isfinite(v) or not 0 < v < 1 for v in p):
        raise ValueError("Invalid fair probability distribution")
    return [v / sum(p) for v in p]


def kelly(probability: float, odds: float, bankroll: float, fraction: float = 0.25, max_pct: float = 100) -> dict:
    if not 0 < probability < 1 or bankroll < 0 or not 0 <= fraction <= 1:
        raise ValueError("Probability, bankroll or Kelly fraction is invalid")
    decimal_odds(odds)
    full = max(0, (probability * odds - 1) / (odds - 1))
    selected = min(full * fraction, max_pct / 100)
    return {"full_fraction": full, "selected_fraction": selected, "full_stake": money(bankroll * full), "stake": money(bankroll * selected), "ev_pct": (probability * odds - 1) * 100}


def fee_for_contracts(q: Quote, count: int) -> float:
    a = q.share_price
    if a is None or q.fee_model == "unknown":
        raise ValueError("Verified contract price and fees are required")
    if q.fee_model == "curve":
        return money(q.fee_rate * count * ((a/q.contract_payout) * (1-a/q.contract_payout)) ** q.fee_exponent * q.contract_payout, up=True)
    if q.fee_model == "per_contract":
        return money(q.fee_rate * count, up=True)
    if q.fee_model == "none":
        return 0
    raise ValueError("Contract fee model is unsupported")


def effective_odds(q: Quote) -> float:
    if q.fee_model == "unknown":
        return 0
    if q.share_price is not None:
        # Continuous approximation only for ranking/sizing. Actual fills use rounded fees below.
        fee = q.fee_rate * ((q.share_price/q.contract_payout) * (1-q.share_price/q.contract_payout)) ** q.fee_exponent * q.contract_payout if q.fee_model == "curve" else q.fee_rate if q.fee_model == "per_contract" else 0
        return q.contract_payout / (q.share_price + fee)
    return 1 + (q.odds - 1) * (1 - q.fee_rate) if q.fee_model == "profit" else q.odds


def position_for_return(q: Quote, target: float) -> dict:
    if q.share_price is not None:
        units = math.ceil(target/q.contract_payout - 1e-9)
        fee = fee_for_contracts(q, units)
        stake = money(units * q.share_price + fee, up=True)
        payout = round(units*q.contract_payout, 8)
    else:
        units = None
        stake = money(target / effective_odds(q), up=True)
        fee = money(stake * (q.odds - 1) * q.fee_rate, up=True) if q.fee_model == "profit" else 0
        payout = money(stake * q.odds - fee)
    return {"book": q.book, "outcome": q.outcome, "selection": q.selection, "odds": q.odds, "effective_odds": effective_odds(q), "stake": stake, "fee": fee, "payout": payout, "contracts": units, "size_verified": q.max_stake is not None or q.contracts is not None, "source_at": q.source_at.isoformat()}


def split_arbitrage(quotes: list[Quote], budget: float, balances: dict | None = None) -> dict | None:
    if not quotes or budget <= 0:
        return None
    if any(q.market_id != quotes[0].market_id for q in quotes):
        raise ValueError("Arbitrage legs must have identical settlement and line identities")
    if sorted(q.outcome for q in quotes) != sorted(quotes[0].outcomes):
        raise ValueError("Arbitrage must cover each outcome exactly once")
    if any(effective_odds(q) <= 1 for q in quotes):
        return None
    implied = sum(1 / effective_odds(q) for q in quotes)
    if implied >= 1:
        return None
    balances = balances or {}
    def fits(legs):
        if sum(l["stake"] for l in legs) > budget + 1e-8:
            return False
        by_book = {}
        for q, l in zip(quotes, legs):
            if q.max_stake is not None and l["stake"] > q.max_stake + 1e-8:
                return False
            if q.contracts is not None and (l["contracts"] or 0) > q.contracts + 1e-8:
                return False
            by_book[q.book] = by_book.get(q.book, 0) + l["stake"]
        return all(book not in balances or amount <= balances[book] + 1e-8 for book, amount in by_book.items())
    lo, hi = 0.0, budget / implied
    for _ in range(64):
        mid = (lo + hi) / 2
        legs = [position_for_return(q, mid) for q in quotes]
        if fits(legs):
            lo = mid
        else:
            hi = mid
    if lo < 0.01:
        return None
    legs = [position_for_return(q, lo) for q in quotes]
    total = round(sum(l["stake"] for l in legs), 2)
    profit = money(min(l["payout"] for l in legs) - total)
    if total <= 0 or profit <= 0 or not fits(legs):
        return None
    return {"legs": legs, "total_stake": total, "profit": profit, "edge": profit / total * 100, "implied_total": implied, "size_verified": all(l["size_verified"] for l in legs)}


def best_arbitrage(candidates: dict[str, list[Quote]], budget: float, balances: dict) -> dict | None:
    # Two/three-way MVP: consider alternate venues when best price has too little capacity.
    groups = [sorted(rows, key=effective_odds, reverse=True)[:4] for rows in candidates.values()]
    best = None
    for combination in product(*groups):
        result = split_arbitrage(list(combination), budget, balances)
        if result and (best is None or result["profit"] > best["profit"]):
            best = result
    return best
