from datetime import UTC, datetime

from latency_bench.events import EventDetector, diff_market, diff_score
from latency_bench.models import (
    BREAK,
    GAME,
    KALSHI,
    LTA_POLL,
    POINT,
    REPRICED,
    RETIREMENT,
    SET,
    WALKOVER,
    digest,
)
from latency_bench.sources.base import make_observation

T0 = datetime(2026, 8, 23, 15, 0, tzinfo=UTC)


def st(sets, games, points, server=1, **kw):
    base = {
        "sets": sets,
        "games": games,
        "points": points,
        "server": server,
        "status": "live",
        "event_status": None,
        "outcome": None,
        "withdrew": None,
    }
    base.update(kw)
    return base


def test_point():
    a = st([0, 0], [[2], [1]], ["15", "0"])
    b = st([0, 0], [[2], [1]], ["30", "0"])
    assert diff_score(a, b) == [(POINT, "point:2-1:30-0")]


def test_game_held_is_not_break():
    a = st([0, 0], [[2], [1]], ["40", "30"], server=1)
    b = st([0, 0], [[3], [1]], ["0", "0"], server=2)
    assert diff_score(a, b) == [(GAME, "game:3-1")]


def test_game_break():
    a = st([0, 0], [[2], [1]], ["30", "40"], server=1)
    b = st([0, 0], [[2], [2]], ["0", "0"], server=2)
    assert diff_score(a, b) == [(GAME, "game:2-2"), (BREAK, "break:2-2")]


def test_set_end_emits_set_and_game():
    a = st([0, 0], [[5], [4]], ["40", "15"], server=1)
    b = st([1, 0], [[6, 0], [4, 0]], ["0", "0"], server=2)
    kinds = [k for k, _ in diff_score(a, b)]
    assert kinds == [SET, GAME]


def test_retirement_and_walkover_from_event_status_not_status():
    a = st([0, 0], [[2], [1]], ["0", "0"])
    b = st([0, 0], [[2], [1]], ["0", "0"], status="completed", event_status="Retired", withdrew=2)
    assert diff_score(a, b) == [(RETIREMENT, "retirement:2")]
    c = st([0, 0], [[], []], [], status="completed")  # lifecycle only: nothing
    assert diff_score(a, c) == []
    d = st([0, 0], [[], []], [], outcome="walkover", withdrew=1)
    assert diff_score(a, d) == [(WALKOVER, "walkover:1")]


def test_first_observation_emits_nothing():
    assert diff_score(None, st([0, 0], [[0], [0]], ["0", "0"])) == []
    assert diff_market(None, {"yes_bid": "0.5"}) == []


def test_market_repriced():
    assert diff_market({"yes_bid": "0.5"}, {"yes_bid": "0.5"}) == []
    assert diff_market({"yes_bid": "0.5"}, {"yes_bid": "0.51"}) == [(REPRICED, "repriced")]


def test_detector_dedupes_identical_polls():
    det = EventDetector()
    s1 = st([0, 0], [[2], [1]], ["15", "0"])
    s2 = st([0, 0], [[2], [1]], ["30", "0"])
    o1 = make_observation(LTA_POLL, "m", "snapshot", s1, "atp", 1.0, T0)
    o1b = make_observation(LTA_POLL, "m", "snapshot", s1, "atp", 2.0, T0)
    o2 = make_observation(LTA_POLL, "m", "snapshot", s2, "atp", 3.0, T0)
    assert det.feed(o1) == []
    assert det.feed(o1b) == []
    ev = det.feed(o2)
    assert [e.kind for e in ev] == [POINT] and ev[0].observed_at_monotonic == 3.0
    k1 = make_observation(KALSHI, "m", "orderbook", {"yes_bid": "0.5"}, "atp", 1.0, T0)
    k2 = make_observation(KALSHI, "m", "orderbook", {"yes_bid": "0.6"}, "atp", 4.0, T0)
    assert det.feed(k1) == [] and det.feed(k2)[0].kind == REPRICED


def test_digest_order_independent():
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
