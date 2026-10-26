"""Fake reference clock adapter for tests. Implements the interface documented in
latency_bench.sources.base.load_reference_adapter. Reads an already-normalized score shape."""

from __future__ import annotations

import asyncio
from typing import Any

from latency_bench.models import REFERENCE
from latency_bench.sources.base import Emit, MatchSpec, call_emit, make_observation

CALLS: list[str] = []


def normalize_event(raw: dict[str, Any]) -> dict[str, Any]:
    sc = raw.get("score") or {}
    return {
        "sets": list(sc.get("sets") or [0, 0]),
        "games": [list(g) for g in (sc.get("games") or [[], []])],
        "points": [str(x) for x in (sc.get("points") or ["", ""])],
        "server": sc.get("server") if sc.get("server") in (1, 2) else None,
        "status": raw.get("status"),
        "event_status": raw.get("event_status"),
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
    for spec in specs:
        if spec.reference_event_id is None:
            continue
        CALLS.append(spec.match_key)
        await call_emit(emit, parse_event({"score": {"sets": [0, 0]}}, spec))
    stop.set()
