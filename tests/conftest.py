"""Shared fixtures for the test suite."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from bot.config import Config, TradingConfig, ValidationConfig
from bot.models import (
    NewsEvent,
    Position,
    PositionState,
    Sentiment,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ── Data fixtures ─────────────────────────────────────────────


@pytest.fixture()
def fixtures_dir():
    return FIXTURES_DIR


@pytest.fixture()
def whitelist_data():
    with open(FIXTURES_DIR / "whitelist.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def keywords_data():
    with open(FIXTURES_DIR / "keywords.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def entities_data():
    with open(FIXTURES_DIR / "entities.json", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def sectors_data():
    with open(FIXTURES_DIR / "sectors.json", encoding="utf-8") as f:
        return json.load(f)


# ── tmp_data_dir — a temp directory with all fixture JSONs ────


@pytest.fixture()
def tmp_data_dir(tmp_path):
    """Copy minimal fixture JSONs into a temp directory for NewsParser."""
    for name in ("keywords.json", "entities.json", "whitelist.json", "sectors.json"):
        src = FIXTURES_DIR / name
        dst = tmp_path / name
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


# ── Config fixture ────────────────────────────────────────────


@pytest.fixture()
def mock_config():
    """Config with a fake token and the test whitelist loaded."""
    cfg = Config(
        tinkoff_token="test-token-fake",
        use_sandbox=True,
        trading=TradingConfig(),
        validation=ValidationConfig(),
    )
    with open(FIXTURES_DIR / "whitelist.json", encoding="utf-8") as f:
        data = json.load(f)
        for item in data["instruments"]:
            cfg.whitelist[item["ticker"]] = item["figi"]
    return cfg


# ── Factory fixtures ──────────────────────────────────────────


@pytest.fixture()
def make_news_event():
    """Factory for NewsEvent with sensible defaults."""

    def _make(
        ticker="SBER",
        figi="BBG004730N88",
        sentiment=Sentiment.BULLISH,
        confidence=0.8,
        keywords_found=None,
        source="test",
        raw_text=None,
    ) -> NewsEvent:
        return NewsEvent(
            ticker=ticker,
            figi=figi,
            sentiment=sentiment,
            confidence=confidence,
            keywords_found=keywords_found or ["test_keyword"],
            timestamp=datetime.now(),
            source=source,
            raw_text=raw_text,
        )

    return _make


@pytest.fixture()
def make_position():
    """Factory for Position with sensible defaults."""

    def _make(
        figi="BBG004730N88",
        ticker="SBER",
        direction=Sentiment.BULLISH,
        entry_price=100.0,
        quantity=10,
        stop_loss=90.0,
        take_profit=115.0,
        atr=5.0,
        peak_price=0.0,
        state=PositionState.ACTIVE,
    ) -> Position:
        return Position(
            figi=figi,
            ticker=ticker,
            direction=direction,
            entry_price=entry_price,
            quantity=quantity,
            entry_time=datetime.now(),
            stop_loss=stop_loss,
            take_profit=take_profit,
            atr=atr,
            peak_price=peak_price,
            state=state,
        )

    return _make
