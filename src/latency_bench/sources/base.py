from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..models import Observation, digest, now_utc

Emit = Callable[[Observation], Awaitable[None] | None]


@dataclass(frozen=True, slots=True)
class MatchSpec:
    """One match under measurement and how each source identifies it.

    match_key: our canonical key (we use the Live Tennis API match id as a string).
    """

    match_key: str
    tour: str | None = None
    lta_match_id: int | None = None
    pm_market_id: str | None = None  # Gamma market id
    pm_token_ids: tuple[str, ...] = ()  # CLOB token ids for the WS market channel
    kalshi_ticker: str | None = None
    sofa_event_id: int | None = None


def stamp() -> tuple[float, Any]:
    """Both clocks, taken together, the instant bytes arrive."""
    return time.monotonic(), now_utc()


def make_observation(
    source: str,
    match_key: str,
    event_kind: str,
    state: dict[str, Any],
    tour: str | None = None,
    mono: float | None = None,
    utc=None,
) -> Observation:
    if mono is None or utc is None:
        mono, utc = stamp()
    return Observation(
        source=source,
        match_key=match_key,
        event_kind=event_kind,
        observed_at_monotonic=mono,
        wallclock_utc=utc,
        payload_digest=digest(state),
        tour=tour,
        state=state,
    )


async def call_emit(emit: Emit, obs: Observation) -> None:
    r = emit(obs)
    if r is not None:
        await r
