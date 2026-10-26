import pytest

from latency_bench.models import KALSHI, LTA_POLL, LTA_WS, PM_GAMMA, PM_WS, REFERENCE
from latency_bench.sources import (
    kalshi_orderbook,
    lta_poll,
    lta_ws,
    polymarket_gamma,
    polymarket_ws,
)
from latency_bench.sources.base import (
    REFERENCE_ADAPTER_ENV,
    REFERENCE_ADAPTER_MISSING,
    ReferenceAdapterError,
    load_reference_adapter,
)

from .conftest import TOKEN


def test_lta_poll_single_object(fx, spec, t0):
    obs = lta_poll.parse_match(fx("lta_match.json"), spec, mono=10.0, utc=t0)
    assert obs.source == LTA_POLL and obs.match_key == "177256" and obs.tour == "atp"
    assert obs.state["sets"] == [1, 0]
    assert obs.state["games"] == [[6, 3], [4, 2]]
    assert obs.state["points"] == ["30", "15"]
    assert obs.state["server"] == 1
    assert obs.observed_at_monotonic == 10.0 and obs.wallclock_utc == t0
    assert len(obs.payload_digest) == 16


def test_lta_poll_list_picks_match(fx, spec):
    obs = lta_poll.parse_match(fx("lta_matches_list.json"), spec)
    assert obs.state["games"] == [[6, 3], [4, 2]]
    other = lta_poll.parse_match(
        fx("lta_matches_list.json"), spec.__class__(match_key="x", lta_match_id=999)
    )
    assert other is None


def test_lta_poll_digest_stable_for_same_state(fx, spec):
    a = lta_poll.parse_match(fx("lta_match.json"), spec)
    b = lta_poll.parse_match(fx("lta_match.json"), spec)
    assert a.payload_digest == b.payload_digest


def test_lta_ws_batch(fx, spec):
    msgs = lta_ws.split_batch(fx("lta_ws_batch.txt"))
    assert len(msgs) == 5 and msgs[-1] == {}
    by_id = {177256: spec}
    out = [o for m in msgs for o in lta_ws.parse_message(m, by_id, mono=1.0, utc=None)]
    kinds = [o.event_kind for o in out]
    assert kinds == ["snapshot", "point_frame", "snapshot"]
    assert all(o.source == LTA_WS for o in out)
    assert out[0].state["points"] == ["40", "15"]
    assert out[1].state == {"seq": 143, "ts": "2026-08-23T15:00:01Z"}
    # point-frame-derived score state pairs with the REST/WS score snapshot
    assert out[2].state["games"] == out[0].state["games"]
    assert out[2].state["points"] == ["40", "15"]


def test_lta_ws_ignores_unknown_match(fx, spec):
    msgs = lta_ws.split_batch(fx("lta_ws_batch.txt"))
    assert [o for m in msgs for o in lta_ws.parse_message(m, {1: spec})] == []


def test_lta_ws_subscribe_frames(fx, spec):
    frames = lta_ws.subscribe_frames(fx("lta_ws_token.json"), [spec])
    chans = [f["subscribe"]["channel"] for f in frames]
    assert chans == ["match:177256", "point:match:177256"]
    assert [f["id"] for f in frames] == [2, 3]
    frames = lta_ws.subscribe_frames({"channels": {"match": "match:{match_id}"}}, [spec])
    assert [f["subscribe"]["channel"] for f in frames] == ["match:177256"]


def test_gamma_market(fx, spec):
    obs = polymarket_gamma.parse_market(fx("gamma_market.json"), spec)
    assert obs.source == PM_GAMMA
    assert obs.state["outcome_prices"] == ["0.65", "0.35"]  # JSON-encoded string decoded
    assert obs.state["best_bid"] == "0.64"
    assert polymarket_gamma.token_ids(fx("gamma_market.json"))[0] == TOKEN
    assert polymarket_gamma.parse_market([fx("gamma_market.json")], spec) is not None
    assert polymarket_gamma.parse_market([], spec) is None


def test_pm_ws_price_change_and_book(fx, spec):
    by_token = {TOKEN: spec}
    out = polymarket_ws.parse_event(fx("pm_ws_price_change.json"), by_token)
    assert len(out) == 1 and out[0].source == PM_WS and out[0].event_kind == "price_change"
    assert out[0].state == {"asset_id": TOKEN, "best_bid": "0.65", "best_ask": "0.67"}
    out = polymarket_ws.parse_message(__import__("json").dumps(fx("pm_ws_book.json")), by_token)
    assert out[0].event_kind == "book"
    assert out[0].state["best_bid"] == "0.6400" and out[0].state["best_ask"] == "0.6600"
    assert polymarket_ws.parse_message("PONG", by_token) == []
    assert polymarket_ws.subscribe_message([TOKEN]) == {"assets_ids": [TOKEN], "type": "market"}


def test_kalshi_orderbook(fx, spec):
    obs = kalshi_orderbook.parse_orderbook(fx("kalshi_orderbook.json"), spec)
    assert obs.source == KALSHI
    assert obs.state == {"yes_bid": "0.3400", "yes_ask": "0.3500"}
    ms = kalshi_orderbook.parse_markets_list(fx("kalshi_markets.json"))
    assert ms[0]["ticker"] == "KXATPMATCH-26AUG23FILTIA-TIA"
    empty = kalshi_orderbook.parse_orderbook({"orderbook_fp": {}}, spec)
    assert empty.state == {"yes_bid": None, "yes_ask": None}


def test_reference_adapter_is_loaded_from_env():
    with pytest.raises(ReferenceAdapterError) as ei:
        load_reference_adapter({})
    assert str(ei.value).startswith(REFERENCE_ADAPTER_MISSING)
    with pytest.raises(ReferenceAdapterError):
        load_reference_adapter({REFERENCE_ADAPTER_ENV: "tests.no_such_adapter"})
    with pytest.raises(ReferenceAdapterError):
        load_reference_adapter({REFERENCE_ADAPTER_ENV: "json"})  # lacks parse_event / run
    mod = load_reference_adapter({REFERENCE_ADAPTER_ENV: "tests.fake_reference"})
    assert callable(mod.parse_event) and callable(mod.run)


def test_reference_clock_only(fx, spec):
    reference = load_reference_adapter({REFERENCE_ADAPTER_ENV: "tests.fake_reference"})
    obs = reference.parse_event(fx("reference_event.json"), spec)
    assert obs.source == REFERENCE
    assert obs.state["sets"] == [1, 0]
    assert obs.state["games"] == [[6, 3], [4, 2]]
    assert obs.state["points"] == ["30", "15"]
    # the normalized state carries no names/ids, nothing of the reference's beyond the score shape
    assert set(obs.state) <= {
        "sets",
        "games",
        "points",
        "server",
        "status",
        "event_status",
        "outcome",
        "withdrew",
    }
