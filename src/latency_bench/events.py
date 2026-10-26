"""Turn consecutive observations of one (source, match) into scoring / repricing events.

Score sources (lta_ws, lta_poll, reference) carry a normalized ScoreState in Observation.state:
    sets:   [p1, p2]                 ints
    games:  [[p1 per set], [p2 per set]]  player-major, like the Live Tennis API
    points: [p1, p2]                 strings ("0","15","30","40","AD" or tiebreak ints as str)
    server: 1 | 2 | None
    status / event_status / outcome / withdrew  (Live Tennis API vocabulary)

Market sources (pm_ws, pm_gamma, kalshi) carry a price dict; any change = "repriced".

Event identity (`event_id`) is built from the *resulting* score so that the same real-world
event gets the same id from every score source, which is what lets aligner.py pair them.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .models import (
    BREAK,
    GAME,
    MARKET_SOURCES,
    POINT,
    REPRICED,
    RETIREMENT,
    SCORE_SOURCES,
    SET,
    WALKOVER,
    Observation,
    ScoreEvent,
)

_RETIRED = {"retired"}
_WALKOVER = {"walk over", "walkover"}


def _has_score_content(state: dict[str, Any]) -> bool:
    """True when a score-source state carries any actual match content."""
    games = state.get("games") or []
    if any(g for g in games):
        return True
    if state.get("points"):
        return True
    return any(
        state.get(k) is not None
        for k in ("server", "status", "event_status", "outcome", "withdrew")
    )


def _sig_games(games: list[list[int]]) -> str:
    p1, p2 = (games + [[], []])[:2]
    n = max(len(p1), len(p2))
    return "|".join(f"{(p1 + [0] * n)[i]}-{(p2 + [0] * n)[i]}" for i in range(n))


def _terminal_kind(state: dict[str, Any]) -> str | None:
    es = (state.get("event_status") or "").strip().lower()
    oc = (state.get("outcome") or "").strip().lower()
    if es in _RETIRED or oc in _RETIRED:
        return RETIREMENT
    if es in _WALKOVER or oc in _WALKOVER:
        return WALKOVER
    return None


def _game_winner(prev_games: list[list[int]], cur_games: list[list[int]]) -> int | None:
    """Which player's per-set game count went up (1|2), else None."""
    for player, (pg, cg) in enumerate(zip(prev_games, cur_games, strict=False), start=1):
        if sum(cg) > sum(pg):
            return player
    # new set started: compare totals across all sets
    tot = [sum(s) for s in cur_games] + [0, 0]
    ptot = [sum(s) for s in prev_games] + [0, 0]
    if tot[0] > ptot[0]:
        return 1
    if tot[1] > ptot[1]:
        return 2
    return None


def diff_score(prev: dict[str, Any] | None, cur: dict[str, Any]) -> list[tuple[str, str]]:
    """Return [(kind, event_id), ...] describing what changed between two score states.

    Rules:
      * retirement / walkover: event_status/outcome flip (never the lifecycle `status`).
      * set: sets array changed.
      * game: total games changed; also `break` when the winner was not the previous server.
      * point: points changed with no game change (the point that ends a game IS the game).
    The first observation (prev is None) emits nothing: we only time *changes* we saw.
    """
    # PointFrame stream: the frame IS the point; sequence increase = one point event.
    if "seq" in cur and "sets" not in cur:
        ps = (prev or {}).get("seq")
        cs = cur.get("seq")
        if isinstance(ps, int) and isinstance(cs, int) and cs > ps:
            return [(POINT, f"point:seq:{cs}")]
        return []  # first sighting (or no increase): we only time changes we saw
    if not _has_score_content(cur):
        return []  # scoreless frame (ping/partial) — never diff it against real states
    if prev is not None and not _has_score_content(prev):
        prev = None
    if prev is None:
        return []
    out: list[tuple[str, str]] = []

    t_prev, t_cur = _terminal_kind(prev), _terminal_kind(cur)
    if t_cur and t_cur != t_prev:
        out.append((t_cur, f"{t_cur}:{cur.get('withdrew') or '?'}"))

    sets_prev, sets_cur = list(prev.get("sets") or [0, 0]), list(cur.get("sets") or [0, 0])
    games_prev = [list(x) for x in (prev.get("games") or [[], []])]
    games_cur = [list(x) for x in (cur.get("games") or [[], []])]
    gsig = _sig_games(games_cur)

    # only an INCREASE in total sets is a set event; anything else is a source-side
    # correction or an alternating partial frame, not tennis happening
    if sum(sets_cur) > sum(sets_prev):
        out.append((SET, f"set:{sets_cur[0]}-{sets_cur[1]}:{gsig}"))

    # only an INCREASE is a game; a decrease is a source-side correction, not an event
    game_changed = sum(map(sum, games_cur)) > sum(map(sum, games_prev))
    if game_changed:
        out.append((GAME, f"game:{gsig}"))
        winner = _game_winner(games_prev, games_cur)
        server = prev.get("server")
        if winner is not None and server in (1, 2) and winner != server:
            out.append((BREAK, f"break:{gsig}"))
    else:
        pts_prev = [str(x) for x in (prev.get("points") or [])]
        pts_cur = [str(x) for x in (cur.get("points") or [])]
        if pts_cur != pts_prev and pts_cur:
            out.append((POINT, f"point:{gsig}:{'-'.join(pts_cur)}"))
    return out


def _fmt4(v: Any) -> str | None:
    """Canonical 4-dp price string ('0.33' and '0.3300' must compare equal)."""
    if v is None or v == "":
        return None
    try:
        return f"{float(v):.4f}"
    except (TypeError, ValueError):
        return str(v)


def canonical_market_state(state: dict[str, Any]) -> dict[str, Any]:
    """Price-only, format-canonical view of a market observation. Sizes and venue
    timestamps are dropped so book-depth jiggle never counts as a reprice."""
    out: dict[str, Any] = {}
    if state.get("asset_id") is not None:
        out["asset_id"] = str(state["asset_id"])
    for k in ("best_bid", "best_ask", "yes_bid", "no_bid", "last_trade_price", "mid", "price"):
        if k in state:
            out[k] = _fmt4(state.get(k))
    return out


def market_stream_key(state: dict[str, Any]) -> tuple[str, str]:
    """(asset_id, shape) so a two-token market or mixed book/trade frames never
    alternate one another's digests into a reprice firehose."""
    shape = "trade" if "last_trade_price" in state else "top"
    return (str(state.get("asset_id") or ""), shape)


def diff_market(prev: dict[str, Any] | None, cur: dict[str, Any]) -> list[tuple[str, str]]:
    """A change in the canonical price view of ONE token stream = one "repriced" event."""
    if prev is None:
        return []
    if canonical_market_state(prev) == canonical_market_state(cur):
        return []
    return [(REPRICED, "repriced")]


class EventDetector:
    """Stateful: feed observations in arrival order, get ScoreEvents out."""

    def __init__(self) -> None:
        self._last: dict[tuple, Observation] = {}

    def feed(self, obs: Observation) -> list[ScoreEvent]:
        if obs.source in MARKET_SOURCES:
            key = (obs.source, obs.match_key, *market_stream_key(obs.state))
        else:
            # PointFrame and score-snapshot frames are separate streams: they must
            # never diff against each other (a pf carries no sets/games).
            shape = "pf" if ("seq" in obs.state and "sets" not in obs.state) else "score"
            key = (obs.source, obs.match_key, shape)
        prev = self._last.get(key)
        if prev is not None and prev.payload_digest == obs.payload_digest:
            return []  # identical re-delivery (e.g. poll returned the same state)
        self._last[key] = obs
        if obs.source in SCORE_SOURCES:
            changes = diff_score(prev.state if prev else None, obs.state)
        elif obs.source in MARKET_SOURCES:
            changes = diff_market(prev.state if prev else None, obs.state)
        else:
            raise ValueError(f"unknown source {obs.source!r}")
        return [
            ScoreEvent(
                source=obs.source,
                match_key=obs.match_key,
                kind=kind,
                event_id=eid,
                observed_at_monotonic=obs.observed_at_monotonic,
                wallclock_utc=obs.wallclock_utc,
                tour=obs.tour,
            )
            for kind, eid in changes
        ]

    def feed_all(self, observations: Iterable[Observation]) -> list[ScoreEvent]:
        out: list[ScoreEvent] = []
        for o in sorted(observations, key=lambda o: o.observed_at_monotonic):
            out.extend(self.feed(o))
        return out
