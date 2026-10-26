import base64
import hashlib
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from sqlalchemy import select

from evhunter.cache import Cache
from evhunter.config import Config
from evhunter.db import CurrentQuote, MarketMapping, initialize, make_engine, transaction
from evhunter.domain import Quote, utcnow
from evhunter.pricing import effective_odds, fee_for_contracts, split_arbitrage
from evhunter.providers import make_prediction_quotes
from evhunter.services import save_quotes
from evhunter.streams import BookUpdate, KalshiBook, NovigBook, PolymarketBook, StreamGap, invalidate, persist_updates, signing_headers


def exemplar(outcome="A", book="fanduel", **kwargs):
    now = utcnow()
    return Quote(**{**dict(event_id="stream-event", sport="NBA", home="A", away="B", starts_at=now+timedelta(hours=2), market="moneyline", ruleset="nba-ot", outcome=outcome, outcomes=["A", "B"], selection=outcome, book=book, odds=2.0, source_at=now, received_at=now), **kwargs})


def mapping(source="kalshi", external="TEST"):
    return dict(market_id=exemplar().market_id, source=source, external_id=external, yes_outcome="A", token_ids=["yes-id", "no-id"], fee_model="none", fee_rate=0, fee_exponent=1, rules_verified=True, fees_reviewed=True)


def kalshi_snapshot(seq=2):
    return {"type": "orderbook_snapshot", "sid": 1, "seq": seq, "sending_ts_ms": 1800000000000, "msg": {"market_ticker": "TEST", "yes_dollars_fp": [["0.42", "100"]], "no_dollars_fp": [["0.55", "75"]]}}


def test_kalshi_snapshot_and_removal_change_executable_asks():
    decoder = KalshiBook()
    update = decoder.process(kalshi_snapshot())[0]
    assert update.books == ([(.45, 75)], [(.58, 100)])
    removed = decoder.process({"type": "orderbook_delta", "sid": 1, "seq": 3, "sending_ts_ms": 1800000000001, "msg": {"market_ticker": "TEST", "side": "no", "price_dollars": ".55", "delta_fp": "-75"}})[0]
    assert removed.books[0] == []


def test_kalshi_gap_requires_a_new_snapshot():
    decoder = KalshiBook()
    decoder.process(kalshi_snapshot())
    with pytest.raises(StreamGap):
        decoder.process({"type": "orderbook_delta", "sid": 1, "seq": 4, "sending_ts_ms": 1800000000001, "msg": {"market_ticker": "TEST", "side": "no", "price_dollars": ".55", "delta_fp": "1"}})


def test_kalshi_delta_before_snapshot_is_rejected():
    with pytest.raises(StreamGap):
        KalshiBook().process({"type": "orderbook_delta", "sid": 1, "seq": 1, "sending_ts_ms": 1800000000001, "msg": {"market_ticker": "TEST", "side": "no", "price_dollars": ".55", "delta_fp": "1"}})


def poly_snapshot(token, price, timestamp="1800000000000"):
    return {"event_type": "book", "market": "condition", "asset_id": token, "asks": [{"price": price, "size": "20"}], "timestamp": timestamp}


def test_polymarket_requires_both_token_snapshots_and_removes_zero_depth():
    decoder = PolymarketBook([mapping("polymarket", "condition")])
    assert decoder.process(poly_snapshot("yes-id", ".45")) == []
    update = decoder.process(poly_snapshot("no-id", ".53"))[0]
    assert update.books == ([(.45, 20)], [(.53, 20)])
    removed = decoder.process({"event_type": "price_change", "market": "condition", "price_changes": [{"asset_id": "yes-id", "price": ".45", "size": "0", "side": "SELL"}], "timestamp": "1800000000001"})[0]
    assert removed.books[0] == []


def test_polymarket_rejects_condition_mismatch_and_ignores_old_data():
    decoder = PolymarketBook([mapping("polymarket", "condition")])
    frame = poly_snapshot("yes-id", ".45")
    decoder.process(frame)
    assert decoder.process(poly_snapshot("yes-id", ".40", "1799999999999")) == []
    with pytest.raises(StreamGap):
        decoder.process({**frame, "market": "wrong"})


def test_novig_orders_are_opposite_side_liquidity_and_close_invalidates():
    decoder = NovigBook([mapping("novig", "novig-market")])
    snapshot = {"ts": 1800000000000, "snapshot": {"novig-market": {"lifecycle": {"seq": 1, "status": "OPEN"}, "book": {"seq": 10, "orders": {"yes-id": [{"order": "one", "price": ".42", "qty": 100}], "no-id": [{"order": "two", "price": ".55", "qty": 150}]}}}}}
    assert decoder.process(snapshot)[0].books == ([(.45, 150)], [(.58, 100)])
    removed = {"ts": 1800000000001, "delta": {"novig-market": {"book": {"seq": 11, "deltas": [{"kind": "remove", "order": "two"}]}}}}
    assert decoder.process(removed)[0].books[0] == []
    closed = {"ts": 1800000000002, "delta": {"novig-market": {"lifecycle": {"seq": 2, "status": "CLOSED"}}}}
    assert decoder.process(closed)[0].books == ([], [])


def test_novig_gap_and_negative_book_are_rejected():
    decoder = NovigBook([mapping("novig", "m")])
    with pytest.raises(StreamGap):
        decoder.process({"ts": 1800000000000, "delta": {"m": {"book": {"seq": 11, "deltas": []}}}})


def test_novig_lifecycle_gap_is_rejected():
    decoder = NovigBook([mapping("novig", "m")])
    decoder.process({"ts": 1800000000000, "snapshot": {"m": {"lifecycle": {"seq": 1, "status": "OPEN"}, "book": {"seq": 1, "orders": {}}}}})
    with pytest.raises(StreamGap):
        decoder.process({"ts": 1800000000001, "delta": {"m": {"lifecycle": {"seq": 3, "status": "OPEN"}}}})


def test_penny_contracts_use_actual_cash_payout_and_depth():
    m = mapping("novig", "m")
    q = make_prediction_quotes(m, exemplar(), ([(.45, 150)], [(.58, 100)]), utcnow())[0]
    assert q.contract_payout == .01
    assert q.share_price == pytest.approx(.0045)
    assert effective_odds(q) == pytest.approx(1/.45)
    arb = split_arbitrage([q, exemplar("B", "draftkings")], 1000)
    assert arb and arb["profit"] > 0
    assert arb["legs"][0]["contracts"] <= 150
    assert arb["legs"][0]["payout"] <= 1.50


def test_penny_contract_fee_is_not_hundred_times_too_large():
    q = make_prediction_quotes({**mapping("novig"), "fee_model": "curve", "fee_rate": .06}, exemplar(), ([(.5, 10000)], []), utcnow())[0]
    assert fee_for_contracts(q, 10000) == 1.5


def write_key(tmp_path, key):
    path = tmp_path/"test-only.pem"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    return str(path)


@pytest.mark.parametrize("kind", ["ed25519", "rsa"])
def test_kalshi_handshake_signature(tmp_path, kind):
    key = ed25519.Ed25519PrivateKey.generate() if kind == "ed25519" else rsa.generate_private_key(public_exponent=65537, key_size=2048)
    config = Config(kalshi_api_key_id="key-id", kalshi_private_key_path=write_key(tmp_path, key), _env_file=None)
    headers = signing_headers("kalshi", config, 1800000000000)
    sig = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    message = b"1800000000000GET/trade-api/ws/v2"
    if kind == "ed25519":
        key.public_key().verify(sig, message)
    else:
        key.public_key().verify(sig, message, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32), hashes.SHA256())


@pytest.mark.parametrize("kind", ["ed25519", "p256"])
def test_novig_handshake_signs_six_lines(tmp_path, kind):
    key = ed25519.Ed25519PrivateKey.generate() if kind == "ed25519" else ec.generate_private_key(ec.SECP256R1())
    config = Config(novig_key_id="read-key", novig_private_key_path=write_key(tmp_path, key), _env_file=None)
    headers = signing_headers("novig", config, 1800000000000)
    message = f"NOVIG-V3\n1800000000000\nGET\n/v3/ws\n\n{hashlib.sha256(b'').hexdigest()}".encode()
    signature = base64.b64decode(headers["Novig-Signature"])
    if kind == "ed25519":
        key.public_key().verify(signature, message)
    else:
        key.public_key().verify(signature, message, ec.ECDSA(hashes.SHA256()))


def test_polymarket_us_ed25519_signature():
    key = ed25519.Ed25519PrivateKey.generate()
    secret = base64.b64encode(key.private_bytes_raw()).decode()
    config = Config(polymarket_us_key_id="key-id", polymarket_us_secret_key=secret, _env_file=None)
    headers = signing_headers("polymarket_us", config, 1800000000000)
    key.public_key().verify(base64.b64decode(headers["X-PM-Signature"]), b"1800000000000GET/v1/ws/markets")


def test_persist_replaces_removed_outcome_and_disconnect_invalidates(tmp_path):
    config = Config(app_env="test", data_mode="live", database_url=f"sqlite:///{tmp_path}/streams.db", redis_url="", embed_worker=False, _env_file=None)
    engine, q = make_engine(config), exemplar()
    initialize(engine, config)
    m = mapping("kalshi", "ticker")
    with transaction(engine) as session:
        session.add(MarketMapping(id="mapping", mode="live", data=m))
    target = {"ticker": [({**m, "id": "mapping"}, q)]}
    cache = Cache(config)
    now = utcnow()
    persist_updates(engine, config, cache, "kalshi", target, {"ticker": BookUpdate("ticker", ([(.45, 100)], [(.58, 100)]), now)})
    with transaction(engine) as session:
        assert len(session.scalars(select(CurrentQuote)).all()) == 2
    persist_updates(engine, config, cache, "kalshi", target, {"ticker": BookUpdate("ticker", ([], [(.58, 100)]), now+timedelta(milliseconds=1))})
    with transaction(engine) as session:
        rows = session.scalars(select(CurrentQuote)).all()
        assert len(rows) == 1 and rows[0].data["outcome"] == "B"
    invalidate(engine, cache, "kalshi", [q.market_id])
    with transaction(engine) as session:
        assert session.scalars(select(CurrentQuote)).first().data["status"] == "disconnected"
    engine.dispose()


def test_aggregator_snapshot_does_not_erase_direct_novig_quotes(tmp_path):
    config = Config(app_env="test", data_mode="live", database_url=f"sqlite:///{tmp_path}/source.db", _env_file=None)
    engine = make_engine(config)
    initialize(engine, config)
    direct = make_prediction_quotes(mapping("novig"), exemplar(), ([(.45, 100)], []), utcnow())[0]
    with transaction(engine) as session:
        save_quotes(session, "live", [direct])
        save_quotes(session, "live", [exemplar()], provider="odds_api", sport="NBA")
        assert any(row.data["book"] == "novig" for row in session.scalars(select(CurrentQuote)).all())
        # The same venue may also be available through the aggregator. Keep its direct depth.
        save_quotes(session, "live", [exemplar(book="novig")])
        row = session.get(CurrentQuote, f"live:{direct.quote_id}")
        assert row.data["feed"] == "direct" and row.data["contract_payout"] == .01
        row.data = {**row.data, "status": "disconnected"}
        session.flush()
        save_quotes(session, "live", [exemplar(book="novig")])
        assert row.data["feed"] == "odds_api"
    engine.dispose()
