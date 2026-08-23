"""Kalshi REST (keyless) — poll a market's orderbook.

VERIFIED 2026-08-23 with read-only GETs against https://api.elections.kalshi.com:
  GET /trade-api/v2/markets?series_ticker=KXATPMATCH&status=open&limit=N
      -> {"cursor": str, "markets": [ {ticker, event_ticker, title, yes_sub_title,
          no_sub_title, status, yes_bid_dollars, yes_ask_dollars, no_bid_dollars,
          no_ask_dollars, last_price_dollars, open_time, close_time, ...} ]}
      e.g. ticker "KXATPMATCH-26AUG23FILTIA-TIA", event_ticker "KXATPMATCH-26AUG23FILTIA"
      (event ticker = series + date + surname prefixes; see kalshi-tennis-live-scores-method post)
  GET /trade-api/v2/markets/{ticker}/orderbook
      -> {"orderbook_fp": {"yes_dollars": [["0.3300","31542.08"], ...],
                           "no_dollars":  [["0.0100","39620.18"], ...]}}
      Levels are [price_dollars, size_fp] strings, ascending by price; the LAST yes level
      equalled the market's yes_bid_dollars in the probe, so best bid = max price per side.

"repriced" = best yes bid / best no bid (== implied yes ask) changed. Market signal only.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..models import KALSHI
from .base import Emit, MatchSpec, call_emit, make_observation

log = logging.getLogger(__name__)
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
SERIES_ATP = "KXATPMATCH"


def _best(levels: list[list[str]]) -> str | None:
    prices = [float(lv[0]) for lv in levels if lv]
    return f"{max(prices):.4f}" if prices else None


def normalize_orderbook(raw: dict[str, Any]) -> dict[str, Any]:
    ob = raw.get("orderbook_fp") or {}
    yes_bid = _best(ob.get("yes_dollars") or [])
    no_bid = _best(ob.get("no_dollars") or [])
    yes_ask = None if no_bid is None else f"{1.0 - float(no_bid):.4f}"
    return {"yes_bid": yes_bid, "yes_ask": yes_ask}


def parse_orderbook(raw: dict[str, Any], spec: MatchSpec, mono=None, utc=None):
    return make_observation(
        KALSHI, spec.match_key, "orderbook", normalize_orderbook(raw), spec.tour, mono, utc
    )


def parse_markets_list(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Minimal projection of GET /markets for ticker discovery (matching lives elsewhere)."""
    out = []
    for m in raw.get("markets") or []:
        out.append(
            {
                "ticker": m.get("ticker"),
                "event_ticker": m.get("event_ticker"),
                "yes_sub_title": m.get("yes_sub_title"),
                "no_sub_title": m.get("no_sub_title"),
                "status": m.get("status"),
                "open_time": m.get("open_time"),
                "close_time": m.get("close_time"),
            }
        )
    return out


async def run(
    emit: Emit, specs: list[MatchSpec], interval_s: float, stop: asyncio.Event, client=None
) -> None:
    import httpx

    own = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        while not stop.is_set():
            for spec in specs:
                if not spec.kalshi_ticker:
                    continue
                try:
                    r = await client.get(f"{BASE_URL}/markets/{spec.kalshi_ticker}/orderbook")
                    r.raise_for_status()
                    await call_emit(emit, parse_orderbook(r.json(), spec))
                except Exception as exc:  # noqa: BLE001
                    log.warning("kalshi %s: %s", spec.match_key, exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            except TimeoutError:
                pass
    finally:
        if own:
            await client.aclose()
