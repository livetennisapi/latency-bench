"""Synthetic multi-source timeline through detector -> aligner -> known deltas."""

from datetime import UTC, datetime, timedelta

from latency_bench.aligner import align
from latency_bench.events import EventDetector
from latency_bench.models import GAME, KALSHI, LTA_POLL, LTA_WS, PM_WS, POINT, REFERENCE
from latency_bench.sources.base import make_observation

T0 = datetime(2026, 8, 23, 15, 0, tzinfo=UTC)


def score(sets, games, points, server=1):
    return {
        "sets": sets,
        "games": games,
        "points": points,
        "server": server,
        "status": "live",
        "event_status": None,
        "outcome": None,
        "withdrew": None,
    }


def build_timeline():
    s0 = score([0, 0], [[2], [1]], ["15", "0"])
    s1 = score([0, 0], [[2], [1]], ["30", "0"])  # point
    s2 = score([0, 0], [[3], [1]], ["0", "0"], server=2)  # game (hold)
    obs = []

    def add(src, t, state, kind="snapshot"):
        obs.append(
            make_observation(src, "m1", kind, state, "wta", 100.0 + t, T0 + timedelta(seconds=t))
        )

    # initial state seen by everyone
    for src in (LTA_WS, LTA_POLL, REFERENCE):
        add(src, 0.0, s0)
    # point: ws at 10.0, reference 10.8, poll 12.5
    add(LTA_WS, 10.0, s1)
    add(REFERENCE, 10.8, s1)
    add(LTA_POLL, 12.5, s1)
    # game: reference first at 40.0, ws 40.3, poll 44.0
    add(REFERENCE, 40.0, s2)
    add(LTA_WS, 40.3, s2)
    add(LTA_POLL, 44.0, s2)
    add(LTA_POLL, 49.0, s2)  # identical re-poll: must not create anything
    # market reprices: pm_ws at 11.5 (after point), kalshi at 41.2 (after game), a stray one at 70
    add(PM_WS, 5.0, {"asset_id": "t", "best_bid": "0.50", "best_ask": "0.52"}, "price_change")
    add(PM_WS, 11.5, {"asset_id": "t", "best_bid": "0.51", "best_ask": "0.53"}, "price_change")
    add(KALSHI, 5.0, {"yes_bid": "0.50", "yes_ask": "0.52"}, "orderbook")
    add(KALSHI, 41.2, {"yes_bid": "0.55", "yes_ask": "0.57"}, "orderbook")
    add(KALSHI, 70.0, {"yes_bid": "0.56", "yes_ask": "0.58"}, "orderbook")
    return obs


def test_known_deltas():
    events = EventDetector().feed_all(build_timeline())
    aligned = align(events, market_window_s=20.0)
    assert [a.kind for a in aligned] == [POINT, GAME]
    point, game = aligned
    assert point.earliest_source == LTA_WS
    assert point.deltas() == {
        LTA_WS: 0.0,
        LTA_POLL: 2.5,
        PM_WS: 1.5,
        "pm_gamma": None,
        KALSHI: None,
        REFERENCE: 0.8,
    }
    assert game.earliest_source == REFERENCE
    assert game.behind(LTA_WS) == 0.3
    assert game.behind(LTA_POLL) == 4.0
    assert game.behind(KALSHI) == 1.2  # attached reprice within window
    assert game.behind(PM_WS) is None  # no pm reprice within 20 s of the game
    assert point.wallclock_utc == T0 + timedelta(seconds=10.0)


def test_market_window_limits_attachment():
    events = EventDetector().feed_all(build_timeline())
    aligned = align(events, market_window_s=1.0)
    point, game = aligned
    assert point.behind(PM_WS) is None  # 1.5 s > 1.0 s window
    assert game.behind(KALSHI) is None


def test_reprice_consumed_once():
    # one reprice cannot serve two score events
    events = EventDetector().feed_all(build_timeline())
    aligned = align(events, market_window_s=100.0)
    point, game = aligned
    assert point.behind(KALSHI) == 31.2  # kalshi 41.2 attached to point (earliest first)
    assert game.behind(KALSHI) == 30.0  # game then gets the 70.0 one
