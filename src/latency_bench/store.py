"""Append-only JSONL of observations so `report` can be re-run offline."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import datetime
from pathlib import Path

from .models import Observation, ScoreEvent


def dump(obs: Observation) -> str:
    return json.dumps(
        {
            "source": obs.source,
            "match_key": obs.match_key,
            "event_kind": obs.event_kind,
            "mono": obs.observed_at_monotonic,
            "utc": obs.wallclock_utc.isoformat(),
            "digest": obs.payload_digest,
            "tour": obs.tour,
            "state": obs.state,
        },
        separators=(",", ":"),
    )


def load(obj: dict) -> Observation:
    return Observation(
        source=obj["source"],
        match_key=obj["match_key"],
        event_kind=obj["event_kind"],
        observed_at_monotonic=obj["mono"],
        wallclock_utc=datetime.fromisoformat(obj["utc"]),
        payload_digest=obj["digest"],
        tour=obj.get("tour"),
        state=obj.get("state") or {},
    )


def write_jsonl(path: Path, observations: Iterable[Observation]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for o in observations:
            fh.write(dump(o) + "\n")


def read_jsonl(path: Path) -> Iterator[Observation]:
    with path.open() as fh:
        for line in fh:
            if line.strip():
                yield load(json.loads(line))


# --- ScoreEvent persistence (what `report` consumes) ---------------------------------------
# Reference clock observations are never persisted; only the *events* detected from them
# (kind + event_id + our timestamps) are, which carries no reference data.


def dump_event(ev: ScoreEvent) -> str:
    return json.dumps(
        {
            "source": ev.source,
            "match_key": ev.match_key,
            "kind": ev.kind,
            "event_id": ev.event_id,
            "mono": ev.observed_at_monotonic,
            "utc": ev.wallclock_utc.isoformat(),
            "tour": ev.tour,
        },
        separators=(",", ":"),
    )


def load_event(obj: dict) -> ScoreEvent:
    return ScoreEvent(
        source=obj["source"],
        match_key=obj["match_key"],
        kind=obj["kind"],
        event_id=obj["event_id"],
        observed_at_monotonic=obj["mono"],
        wallclock_utc=datetime.fromisoformat(obj["utc"]),
        tour=obj.get("tour"),
    )


def write_events_jsonl(path: Path, events: Iterable[ScoreEvent]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        for e in events:
            fh.write(dump_event(e) + "\n")


def read_events_jsonl(path: Path) -> Iterator[ScoreEvent]:
    with path.open() as fh:
        for line in fh:
            if line.strip():
                yield load_event(json.loads(line))
