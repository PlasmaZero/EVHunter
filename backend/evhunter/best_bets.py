"""Explainable research grades, kept separate from outcome probabilities and EV."""
import math
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator
from sqlalchemy import select

from .db import BetResearch
from .domain import as_utc, stable_id, utcnow
from .pricing import devig

VERSION = "research-v1"
WEIGHTS = {"ev": 35, "matchup": 20, "injuries": 15, "market": 15, "execution": 15}
METHOD = {
    "version": VERSION, "weights": WEIGHTS,
    "description": "A research-priority score, not a win probability or a calibrated betting model. Matchup and injury inputs are owner-supplied research; source links are not independently verified.",
    "freshness": {"scoring_hours": 168, "matchup_hours": 24, "injury_hours": 6},
    "caps": {"incomplete_research": 69, "material_injury_risk": 59, "unresolved_injury_risk": 49},
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


def checked_time(value):
    if value is None:
        return value
    if value.tzinfo is None:
        raise ValueError("Research timestamps must include a timezone")
    value = as_utc(value)
    if value > utcnow()+timedelta(minutes=5):
        raise ValueError("Research cannot be dated in the future")
    return value


class ScoringAverages(StrictModel):
    home_for: float = Field(ge=0, le=300)
    home_against: float = Field(ge=0, le=300)
    away_for: float = Field(ge=0, le=300)
    away_against: float = Field(ge=0, le=300)
    home_games: int = Field(ge=1, le=100)
    away_games: int = Field(ge=1, le=100)
    source_url: HttpUrl
    as_of: datetime
    _time = field_validator("as_of")(checked_time)


class ResearchInput(StrictModel):
    market_id: str = Field(pattern=r"^[a-f0-9]{24,32}$")
    outcome: str = Field(min_length=1, max_length=120)
    scoring: ScoringAverages | None = None
    matchup: Literal["unknown", "favorable", "neutral", "unfavorable"] = "unknown"
    matchup_notes: str = Field(default="", max_length=2000)
    matchup_source_url: HttpUrl | None = None
    matchup_as_of: datetime | None = None
    injury_status: Literal["unknown", "clear", "manageable", "material", "unresolved"] = "unknown"
    injury_notes: str = Field(default="", max_length=2000)
    injury_source_url: HttpUrl | None = None
    injury_as_of: datetime | None = None
    _times = field_validator("matchup_as_of", "injury_as_of")(checked_time)

    @model_validator(mode="after")
    def evidence_required(self):
        if self.matchup != "unknown" and not (self.matchup_notes and self.matchup_source_url and self.matchup_as_of):
            raise ValueError("A matchup assessment requires notes, a source URL, and its as-of time")
        if self.injury_status != "unknown" and not (self.injury_notes and self.injury_source_url and self.injury_as_of):
            raise ValueError("An injury assessment requires notes, a source URL, and its as-of time")
        return self


def research_key(market_id, outcome):
    return stable_id(market_id, outcome)


def fresh(stamp, hours, now):
    if not stamp:
        return False
    age = (now-as_utc(stamp)).total_seconds()
    return -300 <= age <= hours*3600


def clamp(value, low=0, high=1):
    return max(low, min(high, value))


def scoring_projection(op, scoring):
    home = (scoring["home_for"]+scoring["away_against"])/2
    away = (scoring["away_for"]+scoring["home_against"])/2
    margin, total = home-away, home+away
    line, outcome = op.get("line"), op["outcome"]
    if op["market"] == "total":
        cushion = total-line if outcome == "over" else line-total
    else:
        home_cushion = margin+(line if op["market"] == "spread" else 0)
        cushion = home_cushion if outcome == op["home"] else -home_cushion
    return {"home": round(home, 2), "away": round(away, 2), "total": round(total, 2), "margin": round(margin, 2), "selection_cushion": round(cushion, 2), "method": "Equal-weight points scored / opponent points allowed; no opponent-strength or personnel adjustment"}


def synthetic_research(op, now):
    """Explicitly fictional examples; never used in live mode or persisted as real research."""
    nba = op["sport"] == "NBA"
    return {
        "market_id": op["market_id"], "outcome": op["outcome"], "origin": "synthetic",
        "scoring": {"home_for": 116 if nba else 26, "home_against": 109 if nba else 20,
                    "away_for": 112 if nba else 22, "away_against": 114 if nba else 25,
                    "home_games": 12 if nba else 6, "away_games": 12 if nba else 6,
                    "source_url": None, "as_of": now.isoformat()},
        "matchup": "favorable", "matchup_notes": "Synthetic example: favorable pace and rotation fit." if nba else "Synthetic example: favorable coverage alignment and protection. Real WR/CB assignments require sourced research.",
        "matchup_source_url": None, "matchup_as_of": now.isoformat(),
        "injury_status": "clear", "injury_notes": "Synthetic injury review for demonstration. This is not a real injury report.",
        "injury_source_url": None, "injury_as_of": now.isoformat(),
    }


def grade_bet(op, pref, research=None, now=None):
    now = now or utcnow()
    r = research or {}
    demo = bool(op.get("is_demo"))
    synthetic = demo and r.get("origin") == "synthetic"
    risks, caps, factors = [], [], []
    assessed = 65  # EV, benchmark/confirmation, and execution are derived from current quotes.

    def factor(key, label, points, reasons, state="assessed"):
        factors.append({"key": key, "label": label, "points": round(points, 1), "max_points": WEIGHTS[key], "state": state, "reasons": reasons})

    edge, p, d = op["edge"], op["true_probability"], op["effective_odds"]
    factor("ev", "Expected value", 35*math.sqrt(clamp(edge/8)), [
        f"Estimated EV {edge:+.2f}% after configured fees: ({p:.5f} × {d:.5f} − 1) × 100.",
        f"Reference probability {p*100:.2f}%; break-even probability {100/d:.2f}%. These estimates are separate from the grade.",
        "EV points scale with the square root of edge / 8%, capped at 35. A larger estimated edge is not proof of model accuracy.",
    ])

    s = r.get("scoring")
    stats_ok = bool(s and fresh(s.get("as_of"), 168, now) and (synthetic or s.get("source_url")))
    projection, matchup_points, matchup_reasons = None, 0, []
    small_sample = False
    if stats_ok:
        assessed += 10
        projection = scoring_projection(op, s)
        small_sample = min(s["home_games"], s["away_games"]) < 4
        scale = 10 if op["sport"] == "NBA" else 6
        matchup_points = 10*clamp(.5+projection["selection_cushion"]/scale)
        if small_sample:
            matchup_points = min(3, matchup_points)
            risks.append("Scoring averages cover fewer than four games for at least one team.")
        matchup_reasons.extend([
            f"{op['home']}: {s['home_for']:.1f} scored / {s['home_against']:.1f} allowed ({s['home_games']} games). {op['away']}: {s['away_for']:.1f} / {s['away_against']:.1f} ({s['away_games']} games).",
            f"Simple scoring baseline: {op['home']} {projection['home']:.1f}, {op['away']} {projection['away']:.1f}; selection cushion {projection['selection_cushion']:+.1f} points against this exact line.",
            "This baseline ignores schedule strength and lineup changes. Its directional score is not a modeled win probability.",
        ])
        if projection["selection_cushion"] < 0:
            risks.append("The simple scoring baseline leans against this selection.")
    else:
        matchup_reasons.append("Scoring averages are missing or older than seven days; no scoring-baseline points awarded.")
        risks.append("Add current points scored / allowed, sample sizes, and a source.")
    matchup_ok = r.get("matchup", "unknown") != "unknown" and fresh(r.get("matchup_as_of"), 24, now) and (synthetic or bool(r.get("matchup_source_url") and r.get("matchup_notes")))
    if matchup_ok:
        assessed += 10
        matchup_points += {"favorable": 10, "neutral": 5, "unfavorable": 0}[r["matchup"]]
        matchup_reasons.append(f"Owner matchup assessment: {r['matchup']}. {r['matchup_notes']}")
        if r["matchup"] == "unfavorable":
            risks.append("Reviewed matchup assessment is unfavorable.")
    else:
        matchup_reasons.append("No current sourced matchup review. Coverage assignments, pace, rest, and usage are not inferred from odds.")
        risks.append("Matchup research is missing or older than 24 hours.")
    factor("matchup", "Matchup & scoring", matchup_points, matchup_reasons, "assessed" if stats_ok and matchup_ok else "incomplete")

    injury_status = r.get("injury_status", "unknown")
    injury_ok = injury_status != "unknown" and fresh(r.get("injury_as_of"), 6, now) and (synthetic or bool(r.get("injury_source_url") and r.get("injury_notes")))
    if injury_ok:
        assessed += 15
        factor("injuries", "Injury report / IR", {"clear": 15, "manageable": 9, "material": 3, "unresolved": 0}[injury_status], [f"Owner injury review: {injury_status}. {r['injury_notes']}", "Review actives, IR, usage restrictions, and late lineup changes. A source link does not independently verify this assessment."])
    else:
        factor("injuries", "Injury report / IR", 0, ["Injury report is missing or older than six hours. Missing information does not mean a healthy lineup."], "incomplete")
        risks.append("Current injury/IR review is required.")
    if injury_status in {"material", "unresolved"}:
        # Expiry cannot erase a known concern or improve an unresolved bet's grade.
        risks.append("Material injury risk affects this selection." if injury_status == "material" else "An important player or workload decision remains unresolved.")
        caps.append({"maximum": 59 if injury_status == "material" else 49, "reason": f"{injury_status.capitalize()} injury risk"})

    reference = op["reference"]
    base = 10 if reference == "pinnacle" else 7 if reference in {"kalshi", "polymarket", "polymarket_us", "novig", "prophetx"} else 4
    benchmark_reasons = [f"Reference: {reference}; devig method: {op['method']}."]
    if base == 4:
        benchmark_reasons.append("This is a retail-book probability proxy, not an independently calibrated probability model.")
        risks.append("EV uses a retail reference; its estimate can be wrong or correlated with other books.")
    grouped = {}
    for q in op["comparison"]:
        if q["book"] in op["books"] or q["book"] == reference or q["fee_model"] != "none":
            continue
        if abs((as_utc(q["source_at"])-as_utc(op["source_at"])).total_seconds()) > pref["max_quote_skew"]:
            continue
        grouped.setdefault(q["book"], {})[q["outcome"]] = q
    outcomes = sorted({q["outcome"] for q in op["comparison"]})
    corroborating, comparisons = 0, []
    for book, quotes in grouped.items():
        if set(quotes) != set(outcomes):
            continue
        try:
            probabilities = devig([quotes[o]["odds"] for o in outcomes], op["method"])
        except ValueError:
            continue
        estimate = probabilities[outcomes.index(op["outcome"])]
        agrees = abs(estimate-p) <= .03
        corroborating += int(agrees)
        comparisons.append(f"{book}: {estimate*100:.2f}% for this selection ({'within' if agrees else 'outside'} 3 percentage points of reference)")
    confirmation = min(5, corroborating*2.5)
    benchmark_reasons.extend(comparisons or ["No additional complete, contemporaneous, fee-free book available for confirmation."])
    benchmark_reasons.append("Cross-book agreement is a consistency check; correlated prices are not independent evidence of accuracy.")
    factor("market", "Reference & confirmation", base+confirmation, benchmark_reasons)

    age = max(0, (now-as_utc(op["source_at"])).total_seconds())
    fresh_points = 10*clamp(1-age/pref["max_quote_age"])
    execution_reasons = [f"Oldest target/reference quote is {age:.0f}s old; your limit is {pref['max_quote_age']}s."]
    execution_reasons.append("Quoted size is available; verify actual acceptance." if op["size_verified"] else "Acceptance limit is unknown. Suggested stake does not prove it will be accepted.")
    if not op["size_verified"]:
        risks.append("Venue acceptance size is unverified.")
    if op["stake"] <= 0:
        risks.append("No funded stake: record bankroll or review stake constraints.")
    factor("execution", "Freshness & execution", fresh_points+(5 if op["size_verified"] else 1), execution_reasons)

    complete = stats_ok and matchup_ok and injury_ok and not small_sample
    if not complete:
        caps.append({"maximum": 69, "reason": "Incomplete, stale, or small-sample research"})
    raw = round(sum(f["points"] for f in factors))
    score = max(0, min([raw, *[c["maximum"] for c in caps]]))
    candidate = complete and injury_status not in {"material", "unresolved"} and op["stake"] > 0 and score >= 70
    state = "candidate" if candidate else "provisional" if not complete else "watch"
    letter = "A" if score >= 85 else "B" if score >= 70 else "C" if score >= 55 else "D"
    return {
        "opportunity": op, "research_key": research_key(op["market_id"], op["outcome"]), "research": research,
        "score": score, "raw_score": raw, "letter": letter, "state": state, "coverage": assessed,
        "factors": factors, "risks": risks, "caps": caps, "projection": projection,
        "summary": f"{edge:+.2f}% estimated EV; " + ("research is current." if complete else "research needs review."),
        "graded_at": now.isoformat(), "version": VERSION, "synthetic_research": synthetic,
    }


def rank_best_bets(session, mode, opportunities, pref):
    now = utcnow()
    records = {row.id: row.data for row in session.scalars(select(BetResearch).where(BetResearch.mode == mode)).all()}
    graded = []
    selections = [op for op in opportunities if op["type"] == "ev"]
    demo_keys = {research_key(op["market_id"], op["outcome"]) for op in selections[:3]} if mode == "demo" else set()
    for op in selections:
        key = research_key(op["market_id"], op["outcome"])
        research = records.get(f"{mode}:{key}")
        if research is None and key in demo_keys:
            research = synthetic_research(op, now)
        graded.append(grade_bet(op, pref, research, now))
    graded.sort(key=lambda g: (-g["score"], -g["opportunity"]["edge"], g["opportunity"]["id"]))
    return {"items": graded, "methodology": METHOD, "as_of": now.isoformat(), "counts": {
        "total": len(graded), "candidates": sum(g["state"] == "candidate" for g in graded),
        "provisional": sum(g["state"] == "provisional" for g in graded), "reviewed": sum(g["coverage"] == 100 for g in graded),
    }}
