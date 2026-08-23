"""Polymarket CLOB WebSocket — market channel (keyless).

CITED from https://docs.polymarket.com/developers/CLOB/websocket/wss-overview (read 2026-08-23):
  URL:        wss://ws-subscriptions-clob.polymarket.com/ws/market
  subscribe:  {"assets_ids": ["<token_id>", ...], "type": "market"}
  messages (event_type):
    book              {event_type, market, asset_id, timestamp, hash, bids[], asks[]}
    price_change      {event_type, market, price_changes:[{asset_id, price, size, side, hash,
                       best_bid, best_ask}], timestamp}
    last_trade_price  {event_type, market, asset_id, price, size, fee_rate_bps, side,
                       timestamp, transaction_hash}
    tick_size_change  {event_type, market, asset_id, old_tick_size, new_tick_size, timestamp}
  timestamps are milliseconds since epoch (venue clock — we do NOT use it for deltas).

ASSUMED: the socket may deliver a JSON array of events in one message (seen in community
clients); we accept both a single object and a list. Keepalive: we send the text "PING"
every ~10s — that convention comes from community clients, not from the page cited above.

"repriced" here = best_bid/best_ask/last price changed for one of the match's token ids.
Polymarket prices never leave this harness; only timing deltas are reported.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..models import PM_WS, Observation
from .base import Emit, MatchSpec, call_emit, make_observation

log = logging.getLogger(__name__)
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def subscribe_message(token_ids: list[str]) -> dict[str, Any]:
    return {"assets_ids": list(token_ids), "type": "market"}


def _top(levels: list[dict[str, Any]], best: str) -> str | None:
    prices = [float(lv["price"]) for lv in levels if "price" in lv]
    if not prices:
        return None
    return f"{(max(prices) if best == 'max' else min(prices)):.4f}"


def parse_event(
    ev: dict[str, Any], specs_by_token: dict[str, MatchSpec], mono=None, utc=None
) -> list[Observation]:
    et = ev.get("event_type")
    out: list[Observation] = []
    if et == "book":
        spec = specs_by_token.get(str(ev.get("asset_id")))
        if spec:
            state = {
                "asset_id": str(ev["asset_id"]),
                "best_bid": _top(ev.get("bids") or [], "max"),
                "best_ask": _top(ev.get("asks") or [], "min"),
            }
            out.append(make_observation(PM_WS, spec.match_key, "book", state, spec.tour, mono, utc))
    elif et == "price_change":
        for pc in ev.get("price_changes") or []:
            spec = specs_by_token.get(str(pc.get("asset_id")))
            if spec:
                state = {
                    "asset_id": str(pc["asset_id"]),
                    "best_bid": pc.get("best_bid"),
                    "best_ask": pc.get("best_ask"),
                }
                out.append(
                    make_observation(
                        PM_WS, spec.match_key, "price_change", state, spec.tour, mono, utc
                    )
                )
    elif et == "last_trade_price":
        spec = specs_by_token.get(str(ev.get("asset_id")))
        if spec:
            state = {"asset_id": str(ev["asset_id"]), "last_trade_price": str(ev.get("price"))}
            out.append(
                make_observation(
                    PM_WS, spec.match_key, "last_trade_price", state, spec.tour, mono, utc
                )
            )
    return out


def parse_message(
    text: str, specs_by_token: dict[str, MatchSpec], mono=None, utc=None
) -> list[Observation]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []  # e.g. "PONG"
    events = data if isinstance(data, list) else [data]
    out: list[Observation] = []
    for ev in events:
        if isinstance(ev, dict):
            out.extend(parse_event(ev, specs_by_token, mono, utc))
    return out


async def run(emit: Emit, specs: list[MatchSpec], stop: asyncio.Event) -> None:
    import websockets

    by_token = {t: s for s in specs for t in s.pm_token_ids}
    if not by_token:
        return
    while not stop.is_set():
        try:
            async with websockets.connect(WS_URL) as ws:
                await ws.send(json.dumps(subscribe_message(list(by_token))))

                async def keepalive():
                    while True:
                        await asyncio.sleep(10)
                        await ws.send("PING")

                ka = asyncio.create_task(keepalive())
                try:
                    while not stop.is_set():
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        for obs in parse_message(
                            raw if isinstance(raw, str) else raw.decode(), by_token
                        ):
                            await call_emit(emit, obs)
                finally:
                    ka.cancel()
        except Exception as exc:  # noqa: BLE001
            log.warning("pm_ws reconnecting after: %s", exc)
            await asyncio.sleep(2)
