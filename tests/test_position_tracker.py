"""Tests for bot/execution/position_tracker.py."""

import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.execution.position_tracker import PositionTracker
from bot.models import ExitSignal, Position, PositionState, Sentiment


# ── helpers ──────────────────────────────────────────────────────


def _make_tracker(mock_config, risk_manager=None, trade_db=None, rate_limiter=None):
    """Build a PositionTracker with a MagicMock client."""
    client = MagicMock()
    rm = risk_manager or _default_risk_manager()
    return PositionTracker(
        client=client,
        config=mock_config,
        risk_manager=rm,
        trade_db=trade_db,
        rate_limiter=rate_limiter,
    )


def _default_risk_manager():
    rm = MagicMock()
    rm.calculate_pnl = MagicMock(return_value=50.0)
    rm.record_trade = MagicMock()
    rm.check_stop_loss = MagicMock(return_value=False)
    rm.check_take_profit = MagicMock(return_value=False)
    rm.check_time_limit = MagicMock(return_value=False)
    rm.check_trailing_stop = MagicMock(return_value=False)
    return rm


# ═══════════════════════════════════════════════════════════════
#  add_position / accessors
# ═══════════════════════════════════════════════════════════════


class TestAddPositionAndAccessors:
    async def test_add_position_stores_in_dict(self, mock_config, make_position):
        tracker = _make_tracker(mock_config)
        pos = make_position(figi="FIGI_A")
        await tracker.add_position(pos)

        assert tracker._positions["FIGI_A"] is pos

    async def test_get_position_returns_stored(self, mock_config, make_position):
        tracker = _make_tracker(mock_config)
        pos = make_position(figi="FIGI_A")
        await tracker.add_position(pos)

        assert tracker.get_position("FIGI_A") is pos

    async def test_get_position_returns_none_for_unknown(self, mock_config):
        tracker = _make_tracker(mock_config)
        assert tracker.get_position("UNKNOWN") is None

    async def test_has_position_true(self, mock_config, make_position):
        tracker = _make_tracker(mock_config)
        await tracker.add_position(make_position(figi="FIGI_A"))

        assert tracker.has_position("FIGI_A") is True

    async def test_has_position_false(self, mock_config):
        tracker = _make_tracker(mock_config)
        assert tracker.has_position("MISSING") is False

    async def test_get_all_positions_returns_list(self, mock_config, make_position):
        tracker = _make_tracker(mock_config)
        p1 = make_position(figi="FIGI_A")
        p2 = make_position(figi="FIGI_B")
        await tracker.add_position(p1)
        await tracker.add_position(p2)

        result = tracker.get_all_positions()
        assert isinstance(result, list)
        assert set(p.figi for p in result) == {"FIGI_A", "FIGI_B"}

    async def test_get_positions_count(self, mock_config, make_position):
        tracker = _make_tracker(mock_config)
        assert tracker.get_positions_count() == 0

        await tracker.add_position(make_position(figi="FIGI_A"))
        assert tracker.get_positions_count() == 1

        await tracker.add_position(make_position(figi="FIGI_B"))
        assert tracker.get_positions_count() == 2


# ═══════════════════════════════════════════════════════════════
#  close_position
# ═══════════════════════════════════════════════════════════════


class TestClosePosition:
    async def test_close_sets_exit_fields(self, mock_config, make_position):
        rm = _default_risk_manager()
        rm.calculate_pnl.return_value = 120.0
        tracker = _make_tracker(mock_config, risk_manager=rm)

        pos = make_position(figi="FIGI_A", entry_price=100.0)
        await tracker.add_position(pos)

        closed = await tracker.close_position("FIGI_A", 112.0, "STOP_LOSS")

        assert closed is not None
        assert closed.is_open is False
        assert closed.state == PositionState.CLOSED
        assert closed.exit_price == 112.0
        assert isinstance(closed.exit_time, datetime)
        assert closed.exit_reason == "STOP_LOSS"
        assert closed.pnl == 120.0

    async def test_close_appends_to_history(self, mock_config, make_position):
        tracker = _make_tracker(mock_config)
        pos = make_position(figi="FIGI_A")
        await tracker.add_position(pos)

        await tracker.close_position("FIGI_A", 105.0, "TAKE_PROFIT")

        assert len(tracker._history) == 1
        assert tracker._history[0] is pos

    async def test_close_calls_risk_manager(self, mock_config, make_position):
        rm = _default_risk_manager()
        tracker = _make_tracker(mock_config, risk_manager=rm)

        pos = make_position(figi="FIGI_A")
        await tracker.add_position(pos)
        await tracker.close_position("FIGI_A", 108.0, "TRAILING_STOP")

        rm.calculate_pnl.assert_called_once_with(pos, 108.0)
        rm.record_trade.assert_called_once_with(50.0)  # default return

    async def test_close_removes_from_positions(self, mock_config, make_position):
        tracker = _make_tracker(mock_config)
        pos = make_position(figi="FIGI_A")
        await tracker.add_position(pos)

        await tracker.close_position("FIGI_A", 105.0, "SL")

        assert tracker.has_position("FIGI_A") is False
        assert tracker.get_positions_count() == 0

    async def test_close_unknown_figi_returns_none(self, mock_config):
        tracker = _make_tracker(mock_config)
        result = await tracker.close_position("NOPE", 100.0, "reason")
        assert result is None

    async def test_close_persists_to_trade_db(self, mock_config, make_position):
        trade_db = MagicMock()
        trade_db.save_position = MagicMock()
        tracker = _make_tracker(mock_config, trade_db=trade_db)

        pos = make_position(figi="FIGI_A")
        await tracker.add_position(pos)
        await tracker.close_position("FIGI_A", 105.0, "TP")

        trade_db.save_position.assert_called_once_with(pos)

    async def test_close_without_trade_db_no_error(self, mock_config, make_position):
        tracker = _make_tracker(mock_config, trade_db=None)
        pos = make_position(figi="FIGI_A")
        await tracker.add_position(pos)

        closed = await tracker.close_position("FIGI_A", 105.0, "TP")
        assert closed is not None  # no exception

    async def test_close_trade_db_error_handled(self, mock_config, make_position):
        trade_db = MagicMock()
        trade_db.save_position = MagicMock(side_effect=RuntimeError("disk full"))
        tracker = _make_tracker(mock_config, trade_db=trade_db)

        pos = make_position(figi="FIGI_A")
        await tracker.add_position(pos)

        # Should not raise; error is printed/caught internally
        closed = await tracker.close_position("FIGI_A", 105.0, "SL")
        assert closed is not None


# ═══════════════════════════════════════════════════════════════
#  _check_static_exit
# ═══════════════════════════════════════════════════════════════


class TestCheckStaticExit:
    def test_returns_stop_loss(self, mock_config, make_position):
        rm = _default_risk_manager()
        rm.check_stop_loss.return_value = True
        tracker = _make_tracker(mock_config, risk_manager=rm)

        pos = make_position()
        assert tracker._check_static_exit(pos, 80.0) == "STOP_LOSS"

    def test_returns_take_profit(self, mock_config, make_position):
        rm = _default_risk_manager()
        rm.check_stop_loss.return_value = False
        rm.check_take_profit.return_value = True
        tracker = _make_tracker(mock_config, risk_manager=rm)

        pos = make_position()
        assert tracker._check_static_exit(pos, 120.0) == "TAKE_PROFIT"

    def test_returns_time_limit(self, mock_config, make_position):
        rm = _default_risk_manager()
        rm.check_stop_loss.return_value = False
        rm.check_take_profit.return_value = False
        rm.check_time_limit.return_value = True
        tracker = _make_tracker(mock_config, risk_manager=rm)

        pos = make_position()
        assert tracker._check_static_exit(pos, 100.0) == "TIME_LIMIT"

    def test_returns_none_when_all_false(self, mock_config, make_position):
        rm = _default_risk_manager()
        tracker = _make_tracker(mock_config, risk_manager=rm)

        pos = make_position()
        assert tracker._check_static_exit(pos, 100.0) is None

    def test_priority_sl_before_tp(self, mock_config, make_position):
        """When both SL and TP trigger, SL wins (checked first)."""
        rm = _default_risk_manager()
        rm.check_stop_loss.return_value = True
        rm.check_take_profit.return_value = True
        rm.check_time_limit.return_value = True
        tracker = _make_tracker(mock_config, risk_manager=rm)

        pos = make_position()
        assert tracker._check_static_exit(pos, 100.0) == "STOP_LOSS"

    def test_priority_tp_before_time(self, mock_config, make_position):
        """When TP and time limit trigger, TP wins (checked before time)."""
        rm = _default_risk_manager()
        rm.check_stop_loss.return_value = False
        rm.check_take_profit.return_value = True
        rm.check_time_limit.return_value = True
        tracker = _make_tracker(mock_config, risk_manager=rm)

        pos = make_position()
        assert tracker._check_static_exit(pos, 100.0) == "TAKE_PROFIT"


# ═══════════════════════════════════════════════════════════════
#  _update_peak
# ═══════════════════════════════════════════════════════════════


class TestUpdatePeak:
    def test_bullish_updates_when_higher(self, make_position):
        pos = make_position(direction=Sentiment.BULLISH, entry_price=100.0)
        # peak_price defaults to entry_price (100.0) via __post_init__
        PositionTracker._update_peak(pos, 110.0)
        assert pos.peak_price == 110.0

    def test_bullish_no_update_when_lower(self, make_position):
        pos = make_position(direction=Sentiment.BULLISH, entry_price=100.0)
        PositionTracker._update_peak(pos, 95.0)
        assert pos.peak_price == 100.0  # unchanged from entry_price

    def test_bullish_no_update_when_equal(self, make_position):
        pos = make_position(direction=Sentiment.BULLISH, entry_price=100.0)
        PositionTracker._update_peak(pos, 100.0)
        assert pos.peak_price == 100.0

    def test_bearish_updates_when_lower(self, make_position):
        pos = make_position(
            direction=Sentiment.BEARISH,
            entry_price=100.0,
            peak_price=100.0,
        )
        PositionTracker._update_peak(pos, 90.0)
        assert pos.peak_price == 90.0

    def test_bearish_no_update_when_higher(self, make_position):
        pos = make_position(
            direction=Sentiment.BEARISH,
            entry_price=100.0,
            peak_price=100.0,
        )
        PositionTracker._update_peak(pos, 110.0)
        assert pos.peak_price == 100.0

    def test_bearish_initializes_peak_when_zero(self, make_position):
        """If peak_price is 0 (or <=0), bearish branch initializes it."""
        pos = make_position(direction=Sentiment.BEARISH, entry_price=100.0)
        pos.peak_price = 0.0  # force zero
        PositionTracker._update_peak(pos, 95.0)
        assert pos.peak_price == 95.0


# ═══════════════════════════════════════════════════════════════
#  get_history / get_stats
# ═══════════════════════════════════════════════════════════════


class TestHistoryAndStats:
    async def test_get_history_returns_from_history(self, mock_config, make_position):
        tracker = _make_tracker(mock_config)
        # Manually add closed positions to _history
        for i in range(5):
            p = make_position(figi=f"F{i}")
            p.pnl = float(i * 10)
            tracker._history.append(p)

        result = tracker.get_history(limit=3)
        assert len(result) == 3
        # Should return last 3
        assert [p.figi for p in result] == ["F2", "F3", "F4"]

    async def test_get_history_default_limit(self, mock_config, make_position):
        tracker = _make_tracker(mock_config)
        for i in range(5):
            p = make_position(figi=f"F{i}")
            tracker._history.append(p)

        result = tracker.get_history()
        assert len(result) == 5

    async def test_get_stats_empty_history(self, mock_config):
        tracker = _make_tracker(mock_config)
        stats = tracker.get_stats()

        assert stats["total_trades"] == 0
        assert stats["win_rate"] == 0.0
        assert stats["total_pnl"] == 0.0
        assert stats["avg_pnl"] == 0.0
        assert stats["best_trade"] == 0.0
        assert stats["worst_trade"] == 0.0

    async def test_get_stats_correct_values(self, mock_config, make_position):
        tracker = _make_tracker(mock_config)

        pnls = [100.0, -50.0, 200.0, -30.0, 80.0]
        for i, pnl_val in enumerate(pnls):
            p = make_position(figi=f"F{i}")
            p.pnl = pnl_val
            tracker._history.append(p)

        stats = tracker.get_stats()

        assert stats["total_trades"] == 5
        # Wins: 100, 200, 80 => 3 out of 5
        assert stats["win_rate"] == pytest.approx(3 / 5)
        assert stats["total_pnl"] == pytest.approx(300.0)
        assert stats["avg_pnl"] == pytest.approx(60.0)
        assert stats["best_trade"] == pytest.approx(200.0)
        assert stats["worst_trade"] == pytest.approx(-50.0)

    async def test_get_stats_includes_open_positions_count(
        self, mock_config, make_position
    ):
        tracker = _make_tracker(mock_config)
        await tracker.add_position(make_position(figi="FIGI_A"))

        p = make_position(figi="FIGI_CLOSED")
        p.pnl = 10.0
        tracker._history.append(p)

        stats = tracker.get_stats()
        assert stats["open_positions"] == 1


# ═══════════════════════════════════════════════════════════════
#  load_history
# ═══════════════════════════════════════════════════════════════


class TestLoadHistory:
    def test_load_history_from_trade_db(self, mock_config, make_position):
        db_positions = [make_position(figi="DB1"), make_position(figi="DB2")]
        trade_db = MagicMock()
        trade_db.load_history.return_value = db_positions
        tracker = _make_tracker(mock_config, trade_db=trade_db)

        tracker.load_history()

        assert len(tracker._history) == 2
        assert tracker._history[0].figi == "DB1"
        assert tracker._history[1].figi == "DB2"

    def test_load_history_no_db(self, mock_config):
        tracker = _make_tracker(mock_config, trade_db=None)
        tracker.load_history()  # should not raise
        assert tracker._history == []

    def test_load_history_prepends_to_existing(self, mock_config, make_position):
        """DB entries come before any already-appended entries."""
        trade_db = MagicMock()
        trade_db.load_history.return_value = [make_position(figi="DB1")]
        tracker = _make_tracker(mock_config, trade_db=trade_db)
        tracker._history.append(make_position(figi="MEM1"))

        tracker.load_history()

        assert [p.figi for p in tracker._history] == ["DB1", "MEM1"]


# ═══════════════════════════════════════════════════════════════
#  stop_monitoring / on_exit
# ═══════════════════════════════════════════════════════════════


class TestStopMonitoringAndOnExit:
    def test_stop_monitoring_sets_flag(self, mock_config):
        tracker = _make_tracker(mock_config)
        tracker._monitoring = True
        tracker.stop_monitoring()
        assert tracker._monitoring is False

    def test_on_exit_registers_callback(self, mock_config):
        tracker = _make_tracker(mock_config)
        cb = AsyncMock()
        tracker.on_exit(cb)
        assert tracker._on_exit is cb

    async def test_emit_exit_calls_callback(self, mock_config, make_position):
        tracker = _make_tracker(mock_config)
        cb = AsyncMock()
        tracker.on_exit(cb)

        pos = make_position()
        signal = ExitSignal(
            figi=pos.figi,
            ticker=pos.ticker,
            reason="STOP_LOSS",
            current_price=90.0,
            should_reverse=False,
            position=pos,
        )
        await tracker._emit_exit(signal)

        cb.assert_awaited_once_with(signal)

    async def test_emit_exit_no_callback_no_error(self, mock_config, make_position):
        tracker = _make_tracker(mock_config)
        # _on_exit is None by default
        pos = make_position()
        signal = ExitSignal(
            figi=pos.figi,
            ticker=pos.ticker,
            reason="TP",
            current_price=110.0,
            should_reverse=False,
            position=pos,
        )
        # Should not raise
        await tracker._emit_exit(signal)
