import csv
import json
from datetime import UTC, datetime

from latency_bench.aligner import AlignedEvent
from latency_bench.models import KALSHI, LTA_POLL, LTA_WS, POINT, REFERENCE
from latency_bench.report import (
    CSV_COLUMNS,
    STATUS_BANNER,
    build_summary,
    render_markdown,
    write_report,
)

T0 = datetime(2026, 8, 23, 15, 0, tzinfo=UTC)


def _aligned():
    out = []
    for i, (ws, poll) in enumerate([(0.0, 2.0), (0.0, 3.0), (0.0, 10.0)]):
        ae = AlignedEvent(
            "m1", "atp", POINT, f"point:{i}", {LTA_WS: 100 + ws, LTA_POLL: 100 + poll}, T0
        )
        out.append(ae)
    sightings = {REFERENCE: 50.0, LTA_WS: 50.4, KALSHI: 51.0}
    out.append(AlignedEvent("m2", "itf", POINT, "point:x", sightings, T0))
    return out


def test_summary_rows():
    s = build_summary(_aligned(), 20.0, now=T0)
    assert s["status"] == STATUS_BANNER
    assert s["n_events"] == 4
    assert s["date_range"] == {"from": T0.isoformat(), "to": T0.isoformat()}
    rows = {(r["tour"], r["event_kind"], r["source"]): r for r in s["rows"]}
    poll = rows[("atp", "point", "lta_poll")]
    assert poll["n"] == 3 and poll["median_s"] == 3.0 and poll["p90_s"] == 8.6 and poll["wins"] == 0
    assert rows[("atp", "point", "lta_ws")]["wins"] == 3
    assert rows[("itf", "point", "reference")]["median_s"] == 0.0
    assert rows[("itf", "point", "kalshi")]["median_s"] == 1.0
    assert s["earliest_by_tour"] == {"atp": {"lta_ws": 3}, "itf": {"reference": 1}}


def test_markdown_mentions_beaten_tour_and_banner():
    md = render_markdown(build_summary(_aligned(), 20.0, now=T0))
    assert STATUS_BANNER in md
    assert "**itf**: reference was earliest" in md
    assert "market repriced" in md
    assert "| atp | point | lta_poll | 3 | 3.000 | 8.600 | 0 |" in md


def test_write_report_files(tmp_path):
    write_report(_aligned(), tmp_path, 20.0)
    with (tmp_path / "events.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0].keys()) == CSV_COLUMNS
    assert rows[0]["t_lta_ws"] == "0.000" and rows[0]["t_lta_poll"] == "2.000"
    assert rows[0]["t_kalshi"] == "" and rows[0]["earliest_source"] == "lta_ws"
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["n_events"] == 4
    assert (tmp_path / "summary.md").read_text().startswith("# Latency summary — STATUS:")


def test_empty_report(tmp_path):
    s = write_report([], tmp_path, 20.0)
    assert s["n_events"] == 0 and s["date_range"] == {"from": None, "to": None}
    assert "none in this sample" in (tmp_path / "summary.md").read_text()
