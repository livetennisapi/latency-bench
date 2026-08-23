"""latency-bench CLI.

  latency-bench ntp
  latency-bench run --matches matches.json --duration 3600 --out out/ [--lta-key ...]
                    [--poll-interval 5] [--market-poll-interval 2] [--sofa]
  latency-bench report --out out/ [--market-window 20]

matches.json: list of MatchSpec dicts, e.g.
  [{"match_key": "177256", "tour": "atp", "lta_match_id": 177256,
    "pm_market_id": "3804316", "pm_token_ids": ["8678...", "1234..."],
    "kalshi_ticker": "KXATPMATCH-26AUG23FILTIA-TIA", "sofa_event_id": null}]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from .aligner import align
from .clock import ntp_check
from .events import EventDetector
from .models import SOFA, Observation
from .report import write_report
from .sources.base import MatchSpec
from .store import read_events_jsonl, write_events_jsonl, write_jsonl

log = logging.getLogger("latency_bench")


def load_specs(path: Path) -> list[MatchSpec]:
    data = json.loads(path.read_text())
    specs = []
    for d in data:
        d = dict(d)
        d["pm_token_ids"] = tuple(d.get("pm_token_ids") or ())
        specs.append(MatchSpec(**d))
    return specs


async def run_bench(args: argparse.Namespace) -> None:
    from .sources import (
        kalshi_orderbook,
        lta_poll,
        lta_ws,
        polymarket_gamma,
        polymarket_ws,
        sofascore,
    )

    specs = load_specs(Path(args.matches))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    offset = ntp_check()
    (out / "run_meta.json").write_text(
        json.dumps({"ntp_offset_s": offset, "matches": len(specs), "duration_s": args.duration})
    )
    detector = EventDetector()
    stop = asyncio.Event()
    obs_path, ev_path = out / "observations.jsonl", out / "events.jsonl"

    async def emit(obs: Observation) -> None:
        events = detector.feed(obs)
        if obs.source != SOFA:  # Sofascore payloads are never persisted
            write_jsonl(obs_path, [obs])
        if events:
            write_events_jsonl(ev_path, events)
            for e in events:
                log.info("%s %s %s %s", e.source, e.match_key, e.kind, e.event_id)

    tasks = [
        asyncio.create_task(polymarket_gamma.run(emit, specs, args.market_poll_interval, stop)),
        asyncio.create_task(polymarket_ws.run(emit, specs, stop)),
        asyncio.create_task(kalshi_orderbook.run(emit, specs, args.market_poll_interval, stop)),
    ]
    key = args.lta_key or os.environ.get("LTA_API_KEY")
    if key:
        tasks.append(asyncio.create_task(lta_poll.run(emit, specs, key, args.poll_interval, stop)))
        if args.lta_ws:
            tasks.append(asyncio.create_task(lta_ws.run(emit, specs, key, stop)))
    else:
        log.warning("no Live Tennis API key (--lta-key / LTA_API_KEY): lta_* sources disabled")
    if args.sofa:
        tasks.append(asyncio.create_task(sofascore.run(emit, specs, args.poll_interval, stop)))

    try:
        await asyncio.sleep(args.duration)
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    do_report(out, args.market_window)


def do_report(out: Path, market_window: float) -> dict:
    ev_path = out / "events.jsonl"
    events = list(read_events_jsonl(ev_path)) if ev_path.exists() else []
    aligned = align(events, market_window_s=market_window)
    summary = write_report(aligned, out, market_window)
    print((out / "summary.md").read_text())
    return summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="latency-bench")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ntp", help="log local clock offset")
    r = sub.add_parser("run", help="measure for --duration seconds")
    r.add_argument("--matches", required=True, help="matches.json (list of MatchSpec)")
    r.add_argument("--duration", type=float, default=3600)
    r.add_argument("--out", default="out")
    r.add_argument("--lta-key", default=None)
    r.add_argument("--lta-ws", action="store_true", help="also open the ULTRA push feed")
    r.add_argument("--poll-interval", type=float, default=5.0)
    r.add_argument("--market-poll-interval", type=float, default=2.0)
    r.add_argument("--market-window", type=float, default=20.0)
    r.add_argument("--sofa", action="store_true", help="enable private Sofascore reference clock")
    rp = sub.add_parser("report", help="rebuild events.csv / summary.json from events.jsonl")
    rp.add_argument("--out", default="out")
    rp.add_argument("--market-window", type=float, default=20.0)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    args = build_parser().parse_args(argv)
    if args.cmd == "ntp":
        off = ntp_check()
        print(f"offset_s={off}")
        return 0
    if args.cmd == "run":
        asyncio.run(run_bench(args))
        return 0
    if args.cmd == "report":
        do_report(Path(args.out), args.market_window)
        return 0
    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
