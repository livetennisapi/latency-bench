"""Normalized records shared by every source adapter."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# Source identifiers. Keep these strings stable: they are column names in events.csv.
LTA_WS = "lta_ws"
LTA_POLL = "lta_poll"
PM_WS = "pm_ws"
PM_GAMMA = "pm_gamma"
KALSHI = "kalshi"
REFERENCE = "reference"

SCORE_SOURCES = frozenset({LTA_WS, LTA_POLL, REFERENCE})
MARKET_SOURCES = frozenset({PM_WS, PM_GAMMA, KALSHI})
ALL_SOURCES = (LTA_WS, LTA_POLL, PM_WS, PM_GAMMA, KALSHI, REFERENCE)

# Scoring-event kinds (score sources) and the single market kind.
POINT = "point"
GAME = "game"
BREAK = "break"
SET = "set"
RETIREMENT = "retirement"
WALKOVER = "walkover"
REPRICED = "repriced"  # market moved; NOT "score arrived"
SCORE_KINDS = (POINT, GAME, BREAK, SET, RETIREMENT, WALKOVER)


def digest(payload: Any) -> str:
    """Stable short digest of a JSON-serialisable payload (order-independent for dicts)."""
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Observation:
    """One thing a source showed us, stamped on the harness's own clocks.

    observed_at_monotonic: time.monotonic() at the moment the bytes were received locally.
        This is the only clock used for cross-source deltas.
    wallclock_utc: datetime.now(UTC) at the same moment; used for date ranges only.
    payload_digest: digest of the *normalized* state so identical re-deliveries dedupe.
    state: normalized state used by events.py (ScoreState-like dict for score sources,
        price dict for market sources). Never contains raw third-party payloads.
    """

    source: str
    match_key: str
    event_kind: str  # raw adapter kind: "snapshot" | "point_frame" | "book" | ...
    observed_at_monotonic: float
    wallclock_utc: datetime
    payload_digest: str
    tour: str | None = None
    state: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScoreEvent:
    """A detected change, with an identity that is comparable across sources."""

    source: str
    match_key: str
    kind: str
    event_id: str  # e.g. "game:6-4|3-2" — same across sources for the same real event
    observed_at_monotonic: float
    wallclock_utc: datetime
    tour: str | None = None


def now_utc() -> datetime:
    return datetime.now(UTC)
