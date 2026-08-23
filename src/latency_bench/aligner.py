"""Per match and per event: first time each source showed it, and seconds behind the earliest.

Score sources are paired by `event_id` (identical for the same real event across sources).
Market sources only ever say "repriced" — there is no score identity to pair on — so a
market reprice is attached to a score event if it is the first reprice observed at or after
the *earliest* score observation of that event, within `market_window_s`. This is what the
public page calls "market repriced", and it is deliberately NOT called "score arrived".

All times are time.monotonic() seconds from the single harness process.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from .models import ALL_SOURCES, MARKET_SOURCES, REPRICED, SCORE_SOURCES, ScoreEvent


@dataclass
class AlignedEvent:
    match_key: str
    tour: str | None
    kind: str
    event_id: str
    first_seen: dict[str, float] = field(default_factory=dict)  # source -> monotonic
    wallclock_utc: datetime | None = None  # earliest observer's wallclock

    @property
    def earliest(self) -> float:
        return min(self.first_seen.values())

    @property
    def earliest_source(self) -> str:
        return min(self.first_seen, key=self.first_seen.__getitem__)

    def behind(self, source: str) -> float | None:
        t = self.first_seen.get(source)
        return None if t is None else round(t - self.earliest, 6)

    def deltas(self) -> dict[str, float | None]:
        return {s: self.behind(s) for s in ALL_SOURCES}


def align(events: Iterable[ScoreEvent], market_window_s: float = 20.0) -> list[AlignedEvent]:
    score: dict[tuple[str, str], AlignedEvent] = {}
    reprices: dict[tuple[str, str], list[tuple[float, datetime]]] = defaultdict(list)

    for ev in sorted(events, key=lambda e: e.observed_at_monotonic):
        if ev.source in MARKET_SOURCES:
            if ev.kind == REPRICED:
                reprices[(ev.match_key, ev.source)].append(
                    (ev.observed_at_monotonic, ev.wallclock_utc)
                )
            continue
        if ev.source not in SCORE_SOURCES:
            raise ValueError(f"unknown source {ev.source!r}")
        k = (ev.match_key, ev.event_id)
        ae = score.get(k)
        if ae is None:
            ae = score[k] = AlignedEvent(ev.match_key, ev.tour, ev.kind, ev.event_id)
        if ev.source not in ae.first_seen:  # first sighting per source only
            ae.first_seen[ev.source] = ev.observed_at_monotonic
        if ae.wallclock_utc is None or ev.wallclock_utc < ae.wallclock_utc:
            ae.wallclock_utc = ev.wallclock_utc
        if ae.tour is None:
            ae.tour = ev.tour

    # Attach the first reprice at/after the earliest score sighting, within the window.
    # Each reprice is consumed by at most one score event (the earliest one it can serve).
    used: dict[tuple[str, str], set[int]] = defaultdict(set)
    for ae in sorted(score.values(), key=lambda a: a.earliest):
        for src in MARKET_SOURCES:
            rs = reprices.get((ae.match_key, src))
            if not rs:
                continue
            times = [t for t, _ in rs]
            i = bisect.bisect_left(times, ae.earliest)
            while i < len(rs) and i in used[(ae.match_key, src)]:
                i += 1
            if i < len(rs) and times[i] - ae.earliest <= market_window_s:
                ae.first_seen[src] = times[i]
                used[(ae.match_key, src)].add(i)
    return sorted(score.values(), key=lambda a: a.earliest)
