"""Tests for bot.utils.trade_db — SQLite trade history persistence."""

from datetime import datetime, timedelta

import pytest

from bot.models import Position, PositionState, Sentiment
from bot.utils.trade_db import TradeDatabase


def _closed_position(**overrides) -> Position:
    """Create a closed position with sensible defaults."""
    defaults = dict(
        figi="BBG004730N88",
        ticker="SBER",
        direction=Sentiment.BULLISH,
        entry_price=100.0,
        quantity=10,
        entry_time=datetime(2026, 4, 1, 10, 0, 0),
        stop_loss=90.0,
        take_profit=115.0,
        atr=5.0,
        peak_price=105.0,
        state=PositionState.CLOSED,
        is_open=False,
        exit_price=110.0,
        exit_time=datetime(2026, 4, 1, 10, 12, 0),
        exit_reason="TAKE_PROFIT",
        pnl=100.0,
    )
    defaults.update(overrides)
    return Position(**defaults)


class TestTradeDatabase:
    def test_open_creates_table(self, tmp_path):
        db = TradeDatabase(tmp_path / "test.db")
        db.open()
        assert db.count() == 0
        db.close()

    def test_save_and_count(self, tmp_path):
        db = TradeDatabase(tmp_path / "test.db")
        db.open()
        db.save_position(_closed_position())
        assert db.count() == 1
        db.save_position(_closed_position(ticker="GAZP", figi="BBG004730RP0"))
        assert db.count() == 2
        db.close()

    def test_save_load_roundtrip(self, tmp_path):
        db = TradeDatabase(tmp_path / "test.db")
        db.open()

        original = _closed_position()
        db.save_position(original)

        loaded = db.load_history()
        assert len(loaded) == 1
        pos = loaded[0]
        assert pos.figi == original.figi
        assert pos.ticker == original.ticker
        assert pos.direction == Sentiment.BULLISH
        assert pos.entry_price == 100.0
        assert pos.quantity == 10
        assert pos.stop_loss == 90.0
        assert pos.take_profit == 115.0
        assert pos.atr == 5.0
        assert pos.peak_price == 105.0
        assert pos.state == PositionState.CLOSED
        assert pos.is_open is False
        assert pos.exit_price == 110.0
        assert pos.exit_reason == "TAKE_PROFIT"
        assert pos.pnl == 100.0
        db.close()

    def test_datetime_roundtrip(self, tmp_path):
        db = TradeDatabase(tmp_path / "test.db")
        db.open()

        entry_time = datetime(2026, 4, 1, 10, 0, 30)
        exit_time = datetime(2026, 4, 1, 10, 15, 45)
        db.save_position(_closed_position(entry_time=entry_time, exit_time=exit_time))

        pos = db.load_history()[0]
        assert pos.entry_time == entry_time
        assert pos.exit_time == exit_time
        db.close()

    def test_sentiment_enum_roundtrip(self, tmp_path):
        db = TradeDatabase(tmp_path / "test.db")
        db.open()

        for sent in (Sentiment.BULLISH, Sentiment.BEARISH):
            db.save_position(_closed_position(direction=sent, ticker=sent.value))

        loaded = db.load_history()
        directions = {p.direction for p in loaded}
        assert Sentiment.BULLISH in directions
        assert Sentiment.BEARISH in directions
        db.close()

    def test_load_history_limit(self, tmp_path):
        db = TradeDatabase(tmp_path / "test.db")
        db.open()

        for i in range(10):
            db.save_position(_closed_position(pnl=float(i)))

        loaded = db.load_history(limit=3)
        assert len(loaded) == 3
        # Most recent should be last (chronological order after reverse)
        assert loaded[-1].pnl == 9.0
        db.close()

    def test_load_history_chronological_order(self, tmp_path):
        db = TradeDatabase(tmp_path / "test.db")
        db.open()

        t1 = datetime(2026, 4, 1, 10, 0)
        t2 = datetime(2026, 4, 1, 11, 0)
        db.save_position(_closed_position(entry_time=t1, pnl=1.0))
        db.save_position(_closed_position(entry_time=t2, pnl=2.0))

        loaded = db.load_history()
        assert loaded[0].pnl == 1.0  # older first
        assert loaded[1].pnl == 2.0  # newer last
        db.close()

    def test_none_take_profit(self, tmp_path):
        db = TradeDatabase(tmp_path / "test.db")
        db.open()

        db.save_position(_closed_position(take_profit=None))
        pos = db.load_history()[0]
        assert pos.take_profit is None
        db.close()

    def test_survives_reopen(self, tmp_path):
        db_path = tmp_path / "test.db"

        db = TradeDatabase(db_path)
        db.open()
        db.save_position(_closed_position())
        db.close()

        # Reopen — data survives
        db2 = TradeDatabase(db_path)
        db2.open()
        assert db2.count() == 1
        loaded = db2.load_history()
        assert loaded[0].ticker == "SBER"
        db2.close()

    def test_position_tracker_integration(self, tmp_path):
        """Verify PositionTracker uses trade_db on close_position."""
        from unittest.mock import MagicMock

        from bot.config import Config, TradingConfig, ValidationConfig
        from bot.execution.position_tracker import PositionTracker

        db = TradeDatabase(tmp_path / "test.db")
        db.open()

        mock_client = MagicMock()
        config = Config(
            tinkoff_token="fake",
            trading=TradingConfig(),
            validation=ValidationConfig(),
        )

        mock_rm = MagicMock()
        mock_rm.calculate_pnl.return_value = 50.0

        tracker = PositionTracker(mock_client, config, mock_rm, trade_db=db)

        pos = Position(
            figi="BBG004730N88", ticker="SBER", direction=Sentiment.BULLISH,
            entry_price=100.0, quantity=10, entry_time=datetime.now(),
            stop_loss=90.0, take_profit=115.0, atr=5.0,
        )
        import asyncio
        asyncio.run(tracker.add_position(pos))
        asyncio.run(tracker.close_position("BBG004730N88", 105.0, "TEST"))

        assert db.count() == 1
        loaded = db.load_history()
        assert loaded[0].exit_reason == "TEST"
        assert loaded[0].pnl == 50.0
        db.close()
