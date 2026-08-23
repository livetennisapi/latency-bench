"""Regressions for the 2026-08-23 live-run firehose: 416k pm_ws 'repriced' events in
25 minutes (two tokens alternating digests + trades + format drift), lta_ws scoreless
frames re-firing set/game, and `set` firing on any difference instead of an increase."""
from latency_bench.events import EventDetector, diff_score
from latency_bench.models import LTA_WS, PM_WS
from latency_bench.sources.base import make_observation


def obs(source, state, mono):
    return make_observation(source, "1", "x", state, "atp", mono, None)


def test_two_token_alternation_is_not_a_reprice():
    d = EventDetector()
    a = {"asset_id": "AAA", "best_bid": "0.33", "best_ask": "0.34"}
    b = {"asset_id": "BBB", "best_bid": "0.66", "best_ask": "0.67"}
    events = []
    for i, st in enumerate([a, b, a, b, a, b, a, b]):
        events += d.feed(obs(PM_WS, dict(st), 1000.0 + i))
    assert events == []  # nothing repriced: each token stream is constant


def test_price_format_drift_is_not_a_reprice():
    d = EventDetector()
    d.feed(obs(PM_WS, {"asset_id": "AAA", "best_bid": "0.33", "best_ask": "0.34"}, 1.0))
    ev = d.feed(obs(PM_WS, {"asset_id": "AAA", "best_bid": "0.3300", "best_ask": "0.3400"}, 2.0))
    assert ev == []
    ev = d.feed(obs(PM_WS, {"asset_id": "AAA", "best_bid": "0.35", "best_ask": "0.36"}, 3.0))
    assert [e.kind for e in ev] == ["repriced"]


def test_trade_and_book_frames_are_separate_streams():
    d = EventDetector()
    d.feed(obs(PM_WS, {"asset_id": "AAA", "best_bid": "0.33", "best_ask": "0.34"}, 1.0))
    d.feed(obs(PM_WS, {"asset_id": "AAA", "last_trade_price": "0.33"}, 2.0))
    ev = d.feed(obs(PM_WS, {"asset_id": "AAA", "best_bid": "0.33", "best_ask": "0.34"}, 3.0))
    assert ev == []  # book stream unchanged; the trade in between must not reset it


def test_size_only_book_change_is_not_a_reprice():
    d = EventDetector()
    top = {"asset_id": "AAA", "best_bid": "0.33", "best_ask": "0.34"}
    d.feed(obs(PM_WS, {**top, "size": "10"}, 1.0))
    ev = d.feed(obs(PM_WS, {**top, "size": "99"}, 2.0))
    assert ev == []


REAL = {"sets": [1, 1], "games": [[6, 6, 2], [7, 3, 1]], "points": ["p1", "p2"], "server": 2,
        "status": None, "event_status": None, "outcome": None, "withdrew": None}
EMPTY = {"sets": [0, 0], "games": [[], []], "points": [], "server": None,
         "status": None, "event_status": None, "outcome": None, "withdrew": None}


def test_scoreless_frames_never_refire_set_or_game():
    d = EventDetector()
    events = []
    for i, st in enumerate([REAL, EMPTY, REAL, EMPTY, REAL]):
        events += d.feed(obs(LTA_WS, dict(st), 10.0 + i))
    assert events == []  # nothing actually changed on court


def test_set_fires_only_on_increase():
    assert diff_score({"sets": [1, 0], "games": [[6, 0], [4, 0]], "server": 1},
                      {"sets": [0, 1], "games": [[6, 0], [4, 0]], "server": 1}) == []
    ev = diff_score({"sets": [1, 0], "games": [[6, 5], [4, 7]], "server": 1},
                    {"sets": [1, 1], "games": [[6, 5], [4, 7]], "server": 1})
    assert [k for k, _ in ev] == ["set"]


def test_point_frame_seq_stream():
    d = EventDetector()
    assert d.feed(obs(LTA_WS, {"seq": 190, "ts": "t"}, 1.0)) == []  # first sighting: no delta
    ev = d.feed(obs(LTA_WS, {"seq": 191, "ts": "t2"}, 2.0))
    assert [(e.kind, e.event_id) for e in ev] == [("point", "point:seq:191")]
    assert d.feed(obs(LTA_WS, {"seq": 191, "ts": "t2b"}, 3.0)) == []  # re-delivery


def test_normalize_reads_nested_score_object():
    """GET /matches (live) nests sets/games/points/server under `score` — verified
    2026-08-23; the first live run polled 1,340 times and never saw a game because
    the normalizer only read top level."""
    from latency_bench.sources.lta_poll import normalize_match

    m = {"id": 1, "status": "live", "event_status": None, "outcome": None,
         "score": {"sets": [0, 0], "games": [[4], [2]], "points": ["40", "40"],
                   "server": 1, "sequence": 45}}
    st = normalize_match(m)
    assert st["games"] == [[4], [2]]
    assert st["points"] == ["40", "40"]
    assert st["server"] == 1
    # top-level fallback still works (WS frames / docs shape)
    st2 = normalize_match({"sets": [1, 0], "games": [[6, 1], [4, 0]], "points": [],
                           "server": 2, "status": "live"})
    assert st2["games"] == [[6, 1], [4, 0]]
