import json
from datetime import UTC, datetime

from latency_bench import cli, store
from latency_bench.models import LTA_POLL, POINT, ScoreEvent
from latency_bench.sources.base import make_observation

T0 = datetime(2026, 8, 23, 15, 0, tzinfo=UTC)


def test_observation_roundtrip(tmp_path):
    o = make_observation(LTA_POLL, "m", "snapshot", {"sets": [0, 0]}, "atp", 1.5, T0)
    store.write_jsonl(tmp_path / "o.jsonl", [o])
    assert list(store.read_jsonl(tmp_path / "o.jsonl")) == [o]


def test_event_roundtrip_and_report_cli(tmp_path, capsys):
    evs = [
        ScoreEvent("lta_ws", "m", POINT, "point:1", 10.0, T0, "atp"),
        ScoreEvent("lta_poll", "m", POINT, "point:1", 12.0, T0, "atp"),
    ]
    store.write_events_jsonl(tmp_path / "events.jsonl", evs)
    assert list(store.read_events_jsonl(tmp_path / "events.jsonl")) == evs
    assert cli.main(["report", "--out", str(tmp_path)]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["n_events"] == 1
    assert "lta_poll | 1 | 2.000" in capsys.readouterr().out


def test_load_specs(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(
        json.dumps([{"match_key": "1", "tour": "atp", "lta_match_id": 1, "pm_token_ids": ["a"]}])
    )
    specs = cli.load_specs(p)
    assert specs[0].pm_token_ids == ("a",) and specs[0].kalshi_ticker is None


def test_ntp_cli(monkeypatch, capsys):
    monkeypatch.setattr(cli, "ntp_check", lambda: -0.001)
    assert cli.main(["ntp"]) == 0
    assert "offset_s=-0.001" in capsys.readouterr().out


def test_run_reference_without_adapter_fails_early(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LATENCY_BENCH_REFERENCE_ADAPTER", raising=False)
    m = tmp_path / "m.json"
    m.write_text(json.dumps([{"match_key": "1", "tour": "atp", "reference_event_id": 7}]))
    out = tmp_path / "out"
    rc = cli.main(["run", "--matches", str(m), "--out", str(out), "--duration", "0", "--reference"])
    assert rc == 2
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert "the private reference clock adapter is not part of this repository" in err[0]
    assert not out.exists()  # refused before any file or network activity
