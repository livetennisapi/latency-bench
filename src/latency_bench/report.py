"""events.csv + summary.json + summary.md from aligned events.

summary.json shape:
{
  "status": "harness only — no numbers published yet",
  "generated_at": iso, "date_range": {"from": iso, "to": iso}, "n_events": int,
  "market_window_s": float,
  "rows": [ {"tour":..., "event_kind":..., "source":..., "n":..., "median_s":..., "p90_s":...,
             "wins": int (times this source was the earliest observer)} ... ],
  "earliest_by_tour": { tour: { source: count } }
}
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from .aligner import AlignedEvent
from .models import ALL_SOURCES

STATUS_BANNER = "harness only — no numbers published yet"
CSV_COLUMNS = ["match_id", "tour", "event_kind", "event_id", "wallclock_utc", "earliest_source"] + [
    f"t_{s}" for s in ALL_SOURCES
]


def _p90(xs: list[float]) -> float:
    if len(xs) == 1:
        return xs[0]
    q = statistics.quantiles(xs, n=10, method="inclusive")
    return q[8]


def write_events_csv(aligned: list[AlignedEvent], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for ae in aligned:
            row = {
                "match_id": ae.match_key,
                "tour": ae.tour or "",
                "event_kind": ae.kind,
                "event_id": ae.event_id,
                "wallclock_utc": ae.wallclock_utc.isoformat() if ae.wallclock_utc else "",
                "earliest_source": ae.earliest_source,
            }
            for s in ALL_SOURCES:
                d = ae.behind(s)
                row[f"t_{s}"] = "" if d is None else f"{d:.3f}"
            w.writerow(row)


def build_summary(
    aligned: list[AlignedEvent], market_window_s: float, now: datetime | None = None
) -> dict:
    buckets: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    wins: dict[tuple[str, str, str], int] = defaultdict(int)
    earliest_by_tour: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    wcs = [ae.wallclock_utc for ae in aligned if ae.wallclock_utc]
    for ae in aligned:
        tour = ae.tour or "unknown"
        es = ae.earliest_source
        earliest_by_tour[tour][es] += 1
        for s, d in ae.deltas().items():
            if d is None:
                continue
            buckets[(tour, ae.kind, s)].append(d)
            if s == es:
                wins[(tour, ae.kind, s)] += 1
    rows = []
    for (tour, kind, s), xs in sorted(buckets.items()):
        rows.append(
            {
                "tour": tour,
                "event_kind": kind,
                "source": s,
                "n": len(xs),
                "median_s": round(statistics.median(xs), 3),
                "p90_s": round(_p90(xs), 3),
                "wins": wins[(tour, kind, s)],
            }
        )
    return {
        "status": STATUS_BANNER,
        "generated_at": (now or datetime.now(UTC)).isoformat(),
        "date_range": {
            "from": min(wcs).isoformat() if wcs else None,
            "to": max(wcs).isoformat() if wcs else None,
        },
        "n_events": len(aligned),
        "market_window_s": market_window_s,
        "rows": rows,
        "earliest_by_tour": {t: dict(v) for t, v in earliest_by_tour.items()},
    }


def render_markdown(summary: dict) -> str:
    dr = summary["date_range"]
    lines = [
        f"# Latency summary — STATUS: {summary['status']}",
        "",
        f"N events: {summary['n_events']} · range: {dr['from']} → {dr['to']} · "
        f"market attach window: {summary['market_window_s']}s",
        "",
        "Seconds behind the earliest observer (0.000 = this source was first). "
        "Market sources (pm_ws, pm_gamma, kalshi) are *market repriced*, never *score arrived*.",
        "",
        "| tour | event | source | N | median s | p90 s | was earliest |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for r in summary["rows"]:
        lines.append(
            f"| {r['tour']} | {r['event_kind']} | {r['source']} | {r['n']} | "
            f"{r['median_s']:.3f} | {r['p90_s']:.3f} | {r['wins']} |"
        )
    beaten = []
    for tour, counts in summary["earliest_by_tour"].items():
        top = max(counts, key=counts.get)
        if top not in ("lta_ws", "lta_poll"):
            beaten.append(f"- **{tour}**: {top} was earliest most often ({counts[top]} events)")
    lines += ["", "## Sources that beat Live Tennis API"]
    lines += beaten or ["- none in this sample (or no sample yet)"]
    return "\n".join(lines) + "\n"


def write_report(aligned: list[AlignedEvent], out_dir: Path, market_window_s: float) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_events_csv(aligned, out_dir / "events.csv")
    summary = build_summary(aligned, market_window_s)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    (out_dir / "summary.md").write_text(render_markdown(summary))
    return summary
