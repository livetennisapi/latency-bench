"""Private reference clock. REFERENCE CLOCK ONLY. OPTIONAL. OFF BY DEFAULT.

#############################################################################################
#  The reference source's terms forbid commercial use / redistribution of its data.         #
#  This adapter exists ONLY so the harness can tell whether Live Tennis API was the earliest #
#  observer of a scoring event. Nothing from the reference source is ever written to         #
#  events.csv, summary.json or the public page except the *timing* of when we saw a change,  #
#  under the column t_reference. Scores, player names, ids and raw payloads from it are      #
#  NEVER stored beyond the in-memory diff and are NEVER shown as a data product. Do not      #
#  "improve" this by persisting its payloads. (PLAN-latency-page-2026-08-23.md, "Truth      #
#  rules".)                                                                                  #
#############################################################################################

ASSUMED (not verified here. No call to the reference source was made while building this):
  GET {BASE_URL}/event/{event_id}  -> {"event": {...}}  with
  homeScore/awayScore {period1..period5, point} and homeTeam/awayTeam; tennis point strings
  and serving side under event.firstToServe / lastPeriod. The normalizer below is written
  against that remembered shape and must be re-checked against a live capture before any run.
  Enable with --reference ; disabled by default.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..models import REFERENCE
from .base import Emit, MatchSpec, call_emit, make_observation

log = logging.getLogger(__name__)
BASE_URL = "https://api.sofascore.com/api/v1"
_PERIODS = ("period1", "period2", "period3", "period4", "period5")


def normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    """Project a reference event to the harness ScoreState; only score shape, nothing else."""
    ev = raw.get("event") or raw
    hs, aw = ev.get("homeScore") or {}, ev.get("awayScore") or {}
    g1 = [int(hs[p]) for p in _PERIODS if p in hs]
    g2 = [int(aw[p]) for p in _PERIODS if p in aw]
    n = max(len(g1), len(g2))
    g1 += [0] * (n - len(g1))
    g2 += [0] * (n - len(g2))
    finished_sets = [i for i in range(n - 1)] if n else []
    sets = [
        sum(1 for i in finished_sets if g1[i] > g2[i]),
        sum(1 for i in finished_sets if g2[i] > g1[i]),
    ]
    status_type = (ev.get("status") or {}).get("type")
    status_desc = ((ev.get("status") or {}).get("description") or "").lower()
    event_status = None
    if "retired" in status_desc:
        event_status = "Retired"
    elif "walkover" in status_desc:
        event_status = "Walk Over"
    return {
        "sets": sets,
        "games": [g1, g2],
        "points": [str(hs.get("point", "")), str(aw.get("point", ""))],
        "server": ev.get("firstToServe") if ev.get("firstToServe") in (1, 2) else None,
        "status": status_type,
        "event_status": event_status,
        "outcome": None,
        "withdrew": None,
    }


def parse_event(raw: dict[str, Any], spec: MatchSpec, mono=None, utc=None):
    return make_observation(
        REFERENCE, spec.match_key, "snapshot", normalize_event(raw), spec.tour, mono, utc
    )


async def run(
    emit: Emit, specs: list[MatchSpec], interval_s: float, stop: asyncio.Event, client=None
) -> None:
    import httpx

    own = client is None
    client = client or httpx.AsyncClient(timeout=10, headers={"User-Agent": "latency-bench/0.1"})
    try:
        while not stop.is_set():
            for spec in specs:
                if spec.reference_event_id is None:
                    continue
                try:
                    r = await client.get(f"{BASE_URL}/event/{spec.reference_event_id}")
                    r.raise_for_status()
                    await call_emit(emit, parse_event(r.json(), spec))
                except Exception as exc:  # noqa: BLE001
                    log.warning("reference %s: %s", spec.match_key, exc)
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            except TimeoutError:
                pass
    finally:
        if own:
            await client.aclose()
