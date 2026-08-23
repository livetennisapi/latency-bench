"""Live Tennis API — ULTRA push feed (Centrifugo v2 JSON protocol).

VERIFIED against https://docs.livetennisapi.com/reference.html (fetched 2026-08-23):
  * GET /ws-token (ULTRA) -> {"token", "expires_in", "ws_url", "channels": {...}}
  * connect:   {"connect": {"token": "<token>"}, "id": 1}
  * subscribe: {"subscribe": {"channel": "match:{id}" | "slate:all"}, "id": 2}
               optional families when listed in `channels`: point:match:{id} / point:slate,
               signal:match:{id} / signal:slate
  * publications: {"push": {"channel": ..., "pub": {"data": <frame>}}}
  * heartbeat: server sends {} ; client must reply {} promptly
  * messages may batch several newline-delimited JSON objects
  * tokens are short-lived: mint a fresh token on every reconnect
  * score frames on match:/slate: are "the same allowlist score objects the polling endpoints
    return" (sets/games/points/server/status/event_status/... + win_probability_p1, danger)
  * PointFrame fields: type, match_id, point{...}, seq (per-match monotonic, never skips),
    ts (CAPTURE time, may be null), pbp_coverage, quality. No replay; catch up via
    GET /matches/{id}/points?after_seq=.

ASSUMED (not shown verbatim in the docs, see README "Assumptions"):
  * The score frame carries the match id under the key "id" (as the REST match object does)
    and/or "match_id"; we accept either.
  * PointFrame.seq / .ts live at the top level next to "point" (the doc table lists seq/ts
    in the PointFrame description; whether they sit inside "point" is not shown). We look in
    both places.
  * ws_url accepts the raw websocket connection with no extra headers.

A point frame is an EVENT: we emit a "point_frame" Observation carrying seq/ts in state, and
also a synthetic score-state observation when the frame includes sets/games/score so the
generic diff in events.py can pair it with the polling sources.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..models import LTA_WS, Observation
from .base import Emit, MatchSpec, call_emit, make_observation
from .lta_poll import BASE_URL, normalize_match

log = logging.getLogger(__name__)


def split_batch(text: str) -> list[dict[str, Any]]:
    """Centrifugo may batch newline-delimited JSON objects in one WS message."""
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def _match_id(frame: dict[str, Any]) -> int | None:
    for k in ("match_id", "id"):
        v = frame.get(k)
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
    return None


def parse_message(
    msg: dict[str, Any], specs_by_lta_id: dict[int, MatchSpec], mono=None, utc=None
) -> list[Observation]:
    """One decoded Centrifugo object -> zero or more Observations.

    Returns [] for connect/subscribe replies and for the {} heartbeat (caller answers it).
    """
    push = msg.get("push")
    if not push:
        return []
    channel = push.get("channel", "")
    frame = (push.get("pub") or {}).get("data")
    if not isinstance(frame, dict):
        return []
    mid = _match_id(frame)
    if mid is None and ":" in channel:
        tail = channel.rsplit(":", 1)[-1]
        mid = int(tail) if tail.isdigit() else None
    spec = specs_by_lta_id.get(mid) if mid is not None else None
    if spec is None:
        return []

    out: list[Observation] = []
    is_point = channel.startswith("point:") or frame.get("type") == "point"
    if is_point:
        point = frame.get("point") if isinstance(frame.get("point"), dict) else frame
        seq = frame.get("seq", point.get("seq"))
        ts = frame.get("ts", point.get("ts"))
        pf_state = {"seq": seq, "ts": ts}
        obs = make_observation(
            LTA_WS, spec.match_key, "point_frame", pf_state, spec.tour, mono, utc
        )
        out.append(obs)
        if "sets" in point or "games" in point:
            score_like = {
                "sets": point.get("sets"),
                "games": point.get("games"),
                "points": point.get("score") or point.get("points"),
                "server": point.get("server"),
            }
            out.append(
                make_observation(
                    LTA_WS,
                    spec.match_key,
                    "snapshot",
                    normalize_match(score_like),
                    spec.tour,
                    obs.observed_at_monotonic,
                    obs.wallclock_utc,
                )
            )
        return out
    out.append(
        make_observation(
            LTA_WS, spec.match_key, "snapshot", normalize_match(frame), spec.tour, mono, utc
        )
    )
    return out


async def mint_token(api_key: str, client=None) -> dict[str, Any]:
    import httpx

    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        r = await client.get(f"{BASE_URL}/ws-token", headers={"X-API-Key": api_key})
        r.raise_for_status()
        return r.json()
    finally:
        if own:
            await client.aclose()


def subscribe_frames(tok: dict[str, Any], specs: list[MatchSpec]) -> list[dict[str, Any]]:
    """Build the Centrifugo subscribe frames we are entitled to, per the channel vocabulary."""
    channels = tok.get("channels") or {}
    frames: list[dict[str, Any]] = []
    i = 2
    for spec in specs:
        if spec.lta_match_id is None:
            continue
        if "match" in channels:
            frames.append({"subscribe": {"channel": f"match:{spec.lta_match_id}"}, "id": i})
            i += 1
        if "point_match" in channels:
            frames.append({"subscribe": {"channel": f"point:match:{spec.lta_match_id}"}, "id": i})
            i += 1
    return frames


async def run(emit: Emit, specs: list[MatchSpec], api_key: str, stop: asyncio.Event) -> None:
    import websockets

    by_id = {s.lta_match_id: s for s in specs if s.lta_match_id is not None}
    while not stop.is_set():
        try:
            tok = await mint_token(api_key)  # fresh token on EVERY reconnect (docs)
            async with websockets.connect(tok["ws_url"]) as ws:
                await ws.send(json.dumps({"connect": {"token": tok["token"]}, "id": 1}))
                for f in subscribe_frames(tok, specs):
                    await ws.send(json.dumps(f))
                while not stop.is_set():
                    raw = await asyncio.wait_for(ws.recv(), timeout=60)
                    for msg in split_batch(raw if isinstance(raw, str) else raw.decode()):
                        if msg == {}:
                            await ws.send("{}")  # heartbeat reply
                            continue
                        for obs in parse_message(msg, by_id):
                            await call_emit(emit, obs)
        except Exception as exc:  # noqa: BLE001
            log.warning("lta_ws reconnecting after: %s", exc)
            await asyncio.sleep(2)
