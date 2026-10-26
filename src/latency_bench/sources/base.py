from __future__ import annotations

import importlib
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from types import ModuleType
from typing import Any

from ..models import Observation, digest, now_utc

Emit = Callable[[Observation], Awaitable[None] | None]

# The private reference clock adapter is not part of this repository. `--reference` loads it
# from the dotted module path in this variable (importlib), e.g. "my_private.reference".
REFERENCE_ADAPTER_ENV = "LATENCY_BENCH_REFERENCE_ADAPTER"
REFERENCE_ADAPTER_MISSING = "the private reference clock adapter is not part of this repository"


class ReferenceAdapterError(RuntimeError):
    """The private reference clock adapter could not be loaded."""


def load_reference_adapter(env: dict[str, str] | None = None) -> ModuleType:
    """Import the private reference clock adapter named by LATENCY_BENCH_REFERENCE_ADAPTER.

    The module must implement the same interface as every adapter in this package:

      parse_event(raw, spec: MatchSpec, mono=None, utc=None) -> Observation
          Pure. Project one raw payload to a normalized ScoreState (see events.py) and wrap
          it with make_observation(REFERENCE, spec.match_key, "snapshot", state, spec.tour,
          mono, utc). The state must carry only the score shape: no names, ids or payloads.
      async run(emit: Emit, specs: list[MatchSpec], interval_s: float, stop: asyncio.Event,
                client=None) -> None
          Owns the network I/O. Polls every spec whose reference_event_id is set, every
          interval_s seconds, and awaits call_emit(emit, parse_event(...)) per response.
          Returns when stop is set.
    """
    env = os.environ if env is None else env
    path = (env.get(REFERENCE_ADAPTER_ENV) or "").strip()
    if not path:
        raise ReferenceAdapterError(f"{REFERENCE_ADAPTER_MISSING} (set {REFERENCE_ADAPTER_ENV})")
    try:
        mod = importlib.import_module(path)
    except ImportError as exc:
        msg = f"cannot import {REFERENCE_ADAPTER_ENV}={path!r}: {exc}"
        raise ReferenceAdapterError(msg) from exc
    missing = [n for n in ("parse_event", "run") if not callable(getattr(mod, n, None))]
    if missing:
        raise ReferenceAdapterError(f"{path} lacks {', '.join(missing)}, see sources/base.py")
    return mod


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
    reference_event_id: int | None = None  # for the private reference clock adapter


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
