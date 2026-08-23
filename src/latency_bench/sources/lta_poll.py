"""Live Tennis API — REST polling (works on free/Basic; cadence = the tier's budget).

Base: https://api.livetennisapi.com/api/public/v1 , header X-API-Key.
GET /matches/{id} returns the match object directly (list endpoints wrap in {data, meta}).
Match fields used (verified against docs.livetennisapi.com/reference.html on 2026-08-23):
players.p1.name / p2.name, sets [p1,p2], games [[p1 per set],[p2 per set]], points [p1,p2],
server 1|2|null, status, event_status, outcome, withdrew, event_status_updated_at.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..models import LTA_POLL
from .base import Emit, MatchSpec, call_emit, make_observation

log = logging.getLogger(__name__)
BASE_URL = "https://api.livetennisapi.com/api/public/v1"


def normalize_match(m: dict[str, Any]) -> dict[str, Any]:
    """Project a Live Tennis API match object to the harness ScoreState dict.

    The live match object nests the score under `score` ({sets, games, points, server,
    sequence, ...}) — verified against GET /matches?status=live on 2026-08-23. Some
    surfaces (WS point frames, docs snippets) carry the same keys at top level, so read
    the nested object first and fall back to top level.
    """
    sc = m.get("score") if isinstance(m.get("score"), dict) else {}
    get = lambda k: sc.get(k) if k in sc else m.get(k)  # noqa: E731
    games = get("games") or [[], []]
    return {
        "sets": [int(x) for x in (get("sets") or [0, 0])],
        "games": [[int(g) for g in side] for side in games],
        "points": [str(p) for p in (get("points") or [])],
        "server": get("server"),
        "status": m.get("status"),
        "event_status": m.get("event_status"),
        "outcome": m.get("outcome"),
        "withdrew": m.get("withdrew"),
        "event_status_updated_at": m.get("event_status_updated_at"),
    }


def parse_match(raw: dict[str, Any], spec: MatchSpec, mono=None, utc=None):
    """Single-resource response (object directly) OR a {data: [...]} list; returns Observation."""
    obj = raw
    if "data" in raw and isinstance(raw["data"], list):
        want = spec.lta_match_id
        obj = next((x for x in raw["data"] if x.get("id") == want), None)
        if obj is None:
            return None
    return make_observation(
        LTA_POLL, spec.match_key, "snapshot", normalize_match(obj), spec.tour, mono, utc
    )


async def run(
    emit: Emit,
    specs: list[MatchSpec],
    api_key: str,
    interval_s: float,
    stop: asyncio.Event,
    client=None,
) -> None:
    import httpx  # network only here

    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    headers = {"X-API-Key": api_key}
    try:
        while not stop.is_set():
            for spec in specs:
                if spec.lta_match_id is None:
                    continue
                try:
                    r = await client.get(f"{BASE_URL}/matches/{spec.lta_match_id}", headers=headers)
                    r.raise_for_status()
                    obs = parse_match(r.json(), spec)  # stamped after bytes arrive
                    if obs:
                        await call_emit(emit, obs)
                except Exception as exc:  # noqa: BLE001 — keep polling through hiccups
                    log.warning("lta_poll %s: %s", spec.match_key, exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            except TimeoutError:
                pass
    finally:
        if own:
            await client.aclose()
