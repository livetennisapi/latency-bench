import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from latency_bench.sources.base import MatchSpec

FIX = Path(__file__).parent / "fixtures"
TOKEN = "86781508277674171575414787475499297243499446595342891715708208023967"


@pytest.fixture
def fx():
    def _load(name: str):
        p = FIX / name
        return json.loads(p.read_text()) if name.endswith(".json") else p.read_text()

    return _load


@pytest.fixture
def spec():
    return MatchSpec(
        match_key="177256",
        tour="atp",
        lta_match_id=177256,
        pm_market_id="3804316",
        pm_token_ids=(TOKEN, "12345"),
        kalshi_ticker="KXATPMATCH-26AUG23FILTIA-TIA",
        sofa_event_id=1,
    )


@pytest.fixture
def t0():
    return datetime(2026, 8, 23, 15, 0, 0, tzinfo=UTC)
