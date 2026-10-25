from datetime import timedelta

import pytest

from evhunter.domain import Quote, utcnow
from evhunter.pricing import decimal_odds, devig, fee_for_contracts, kelly, money, split_arbitrage


def quote(outcome="A", odds=2.1, **kwargs):
    now = utcnow()
    values = dict(event_id="test", sport="NBA", home="A", away="B", starts_at=now+timedelta(hours=2), market="moneyline", ruleset="nba-ot", outcome=outcome, outcomes=["A", "B"], selection=outcome, book="fanduel" if outcome == "A" else "draftkings", odds=odds, source_at=now, received_at=now)
    return Quote(**{**values, **kwargs})


@pytest.mark.parametrize("value,expected", [(-110, 1+100/110), (110, 2.1), (100, 2), (-250, 1.4)])
def test_american_conversion(value, expected):
    assert decimal_odds(value, "american") == pytest.approx(expected)


@pytest.mark.parametrize("value,format", [(99, "american"), (-99, "american"), (1, "decimal"), (float("nan"), "decimal")])
def test_reject_invalid_odds(value, format):
    with pytest.raises(ValueError):
        decimal_odds(value, format)


@pytest.mark.parametrize("method", ["proportional", "power", "shin"])
def test_balanced_reference_is_half(method):
    assert devig([1.91, 1.91], method) == pytest.approx([.5, .5])


@pytest.mark.parametrize("method", ["proportional", "power", "shin"])
def test_three_way_distribution(method):
    p = devig([2.4, 3.1, 3.4], method)
    assert sum(p) == pytest.approx(1)
    assert p[0] > p[1] > p[2] > 0


def test_shin_binary_matches_additive_solution():
    q = [1/1.8, 1/2.2]
    expected = [p-(sum(q)-1)/2 for p in q]
    assert devig([1.8, 2.2], "shin") == pytest.approx(expected, abs=1e-8)


def test_shin_does_not_silently_accept_underround():
    with pytest.raises(ValueError, match="overround"):
        devig([2.1, 2.1], "shin")


def test_kelly_and_no_edge():
    result = kelly(.55, 2, 10000, .25)
    assert result["full_stake"] == 1000
    assert result["stake"] == 250
    assert kelly(.4, 2, 10000)["stake"] == 0
    assert kelly(.55, 2, 10000, .25, 1)["stake"] == 100


def test_conservative_rounding_ignores_binary_float_noise():
    assert money(100*.91) == 91
    assert money(.07*100*.5*.5, up=True) == 1.75
    assert money(1.234) == 1.23
    assert money(1.234, up=True) == 1.24


def test_two_way_arb_after_cent_rounding():
    arb = split_arbitrage([quote(), quote("B", 2.05)], 1000)
    assert arb is not None
    assert arb["total_stake"] <= 1000
    assert arb["profit"] == pytest.approx(37.34)
    assert all(l["payout"]-arb["total_stake"] >= arb["profit"]-1e-8 for l in arb["legs"])


def test_three_way_arb_covers_all_outcomes():
    rows = [quote(o, 3.2, outcomes=["A", "B", "draw"]) for o in ["A", "B", "draw"]]
    arb = split_arbitrage(rows, 100)
    assert arb and len(arb["legs"]) == 3 and arb["profit"] > 0


def test_reject_non_complementary_lines():
    with pytest.raises(ValueError, match="identical settlement"):
        split_arbitrage([quote(market="total", line=220.5), quote("B", market="total", line=221.5)], 1000)


def test_missing_outcome_is_not_an_arb():
    with pytest.raises(ValueError, match="each outcome"):
        split_arbitrage([quote()], 1000)


def test_liquidity_and_venue_cash_bound_allocation():
    arb = split_arbitrage([quote(max_stake=50), quote("B", 2.05)], 1000, {"draftkings": 40})
    assert arb
    assert arb["legs"][0]["stake"] <= 50
    assert arb["legs"][1]["stake"] <= 40


def test_exchange_fee_can_remove_an_apparent_arb():
    a = quote(odds=1/.49, share_price=.49, fee_model="curve", fee_rate=.07, contracts=100)
    b = quote("B", odds=1/.50, share_price=.50, fee_model="curve", fee_rate=.07, contracts=100)
    assert 1/a.odds+1/b.odds < 1
    assert split_arbitrage([a, b], 1000) is None


def test_contract_rounding_and_fees_cannot_exceed_depth():
    a = quote(odds=1/.45, share_price=.45, fee_model="curve", fee_rate=.07, contracts=10)
    b = quote("B", odds=2.1, max_stake=100)
    arb = split_arbitrage([a, b], 1000)
    assert arb and arb["legs"][0]["contracts"] <= 10
    assert arb["legs"][0]["fee"] == fee_for_contracts(a, arb["legs"][0]["contracts"])


def test_unknown_fees_never_assumed_zero():
    assert split_arbitrage([quote(fee_model="unknown"), quote("B", 2.1)], 100) is None
