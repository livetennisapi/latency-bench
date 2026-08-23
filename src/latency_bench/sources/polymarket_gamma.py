"""Polymarket Gamma REST (keyless) — poll a market's prices.

VERIFIED 2026-08-23 with read-only GETs against https://gamma-api.polymarket.com:
  GET /markets?limit=..&active=true&closed=false  -> JSON list of market objects
  GET /events?...                                  -> list of events, each with "markets": [...]
  market fields: id, question, slug, conditionId, outcomes (JSON-encoded string),
  outcomePrices (JSON-encoded string, e.g. '["0.335", "0.665"]'), clobTokenIds (JSON-encoded
  string list), lastTradePrice, bestBid, bestAsk, updatedAt, gameStartTime, sportsMarketType.
  Tennis match markets look like: question "ITF M15 Vienna Men: A vs B", outcomes = player names.

Matching a Gamma market to a Live Tennis API match is the job of the `polymarket-tennis`
package (plan §Harness); this adapter only needs the market id / token ids from a MatchSpec.

Prices are a *market* signal: a change here is "market repriced", not "score arrived".
Only timing deltas leave this harness — Polymarket prices are never published (ToS 2026-08-11).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from ..models import PM_GAMMA
from .base import Emit, MatchSpec, call_emit, make_observation

log = logging.getLogger(__name__)
BASE_URL = "https://gamma-api.polymarket.com"


def _jsonish(v: Any) -> list:
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            return []
    return list(v or [])


def normalize_market(m: dict[str, Any]) -> dict[str, Any]:
    prices = [str(p) for p in _jsonish(m.get("outcomePrices"))]
    return {
        "outcome_prices": prices,
        "best_bid": None if m.get("bestBid") is None else str(m["bestBid"]),
        "best_ask": None if m.get("bestAsk") is None else str(m["bestAsk"]),
        "last_trade_price": (None if m.get("lastTradePrice") is None else str(m["lastTradePrice"])),
    }


def token_ids(m: dict[str, Any]) -> list[str]:
    return [str(t) for t in _jsonish(m.get("clobTokenIds"))]


def parse_market(raw: dict[str, Any] | list, spec: MatchSpec, mono=None, utc=None):
    """Accept a single market object or a list (GET /markets) and pick spec.pm_market_id."""
    obj: dict[str, Any] | None = raw if isinstance(raw, dict) else None
    if isinstance(raw, list):
        obj = next((x for x in raw if str(x.get("id")) == str(spec.pm_market_id)), None)
    if obj is None:
        return None
    return make_observation(
        PM_GAMMA, spec.match_key, "price", normalize_market(obj), spec.tour, mono, utc
    )


async def run(
    emit: Emit, specs: list[MatchSpec], interval_s: float, stop: asyncio.Event, client=None
) -> None:
    import httpx

    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        while not stop.is_set():
            for spec in specs:
                if not spec.pm_market_id:
                    continue
                try:
                    r = await client.get(f"{BASE_URL}/markets/{spec.pm_market_id}")
                    r.raise_for_status()
                    obs = parse_market(r.json(), spec)
                    if obs:
                        await call_emit(emit, obs)
                except Exception as exc:  # noqa: BLE001
                    log.warning("pm_gamma %s: %s", spec.match_key, exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            except TimeoutError:
                pass
    finally:
        if own:
            await client.aclose()
