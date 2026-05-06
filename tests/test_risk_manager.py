"""Tests for bot.execution.risk_manager — risk calculations and limits."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from bot.execution.risk_manager import RiskManager
from bot.models import Position, PositionState, Sentiment


# ── helpers ──────────────────────────────────────────────────


def _make_rm(mock_config):
    """Create a RiskManager with a MagicMock client (pure methods don't call it)."""
    client = MagicMock()
    return RiskManager(client=client, config=mock_config)


def _make_quotation(value: float):
    """Tiny mock that quacks like a Quotation(units, nano)."""
    q = MagicMock()
    q.units = int(value)
    q.nano = int(round((value - int(value)) * 1e9))
    return q


def _make_candle(open_: float, high: float, low: float, close: float):
    c = MagicMock()
    c.open = _make_quotation(open_)
    c.high = _make_quotation(high)
    c.low = _make_quotation(low)
    c.close = _make_quotation(close)
    return c


# ── calculate_position_size ──────────────────────────────────


class TestCalculatePositionSize:
    def test_basic(self, mock_config):
        rm = _make_rm(mock_config)
        assert rm.calculate_position_size(price=100, lot_size=1, max_rub=1000) == 10

    def test_with_lot_size(self, mock_config):
        rm = _make_rm(mock_config)
        # lot_cost = 100 * 10 = 1000; lots = 10000 / 1000 = 10
        assert rm.calculate_position_size(price=100, lot_size=10, max_rub=10000) == 10

    def test_expensive_instrument_returns_0(self, mock_config):
        rm = _make_rm(mock_config)
        assert rm.calculate_position_size(price=60000, lot_size=1, max_rub=50000) == 0

    def test_zero_price_returns_0(self, mock_config):
        rm = _make_rm(mock_config)
        assert rm.calculate_position_size(price=0, lot_size=1, max_rub=1000) == 0

    def test_uses_config_max_when_max_rub_none(self, mock_config):
        rm = _make_rm(mock_config)
        # default max_position_rub = 50000; price=100, lot=1 -> 500 lots
        result = rm.calculate_position_size(price=100, lot_size=1)
        assert result == 500


# ── calculate_stop_loss ──────────────────────────────────────


class TestCalculateStopLoss:
    def test_bullish(self, mock_config):
        rm = _make_rm(mock_config)
        # 100 - 5*2 = 90
        assert rm.calculate_stop_loss(100, Sentiment.BULLISH, atr=5) == 90.0

    def test_bearish(self, mock_config):
        rm = _make_rm(mock_config)
        # 100 + 5*2 = 110
        assert rm.calculate_stop_loss(100, Sentiment.BEARISH, atr=5) == 110.0

    def test_custom_multiplier(self, mock_config):
        rm = _make_rm(mock_config)
        # 100 - 5*3 = 85
        assert rm.calculate_stop_loss(100, Sentiment.BULLISH, atr=5, multiplier=3) == 85.0

    def test_neutral_returns_entry(self, mock_config):
        rm = _make_rm(mock_config)
        assert rm.calculate_stop_loss(100, Sentiment.NEUTRAL, atr=5) == 100.0


# ── calculate_take_profit ────────────────────────────────────


class TestCalculateTakeProfit:
    def test_bullish(self, mock_config):
        rm = _make_rm(mock_config)
        # 100 + 5*3 = 115
        assert rm.calculate_take_profit(100, Sentiment.BULLISH, atr=5) == 115.0

    def test_bearish(self, mock_config):
        rm = _make_rm(mock_config)
        # 100 - 5*3 = 85
        assert rm.calculate_take_profit(100, Sentiment.BEARISH, atr=5) == 85.0

    def test_custom_multiplier(self, mock_config):
        rm = _make_rm(mock_config)
        # 100 + 5*4 = 120
        assert rm.calculate_take_profit(100, Sentiment.BULLISH, atr=5, multiplier=4) == 120.0

    def test_neutral_returns_entry(self, mock_config):
        rm = _make_rm(mock_config)
        assert rm.calculate_take_profit(100, Sentiment.NEUTRAL, atr=5) == 100.0


# ── check_stop_loss ──────────────────────────────────────────


class TestCheckStopLoss:
    def test_long_hits_sl(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BULLISH, stop_loss=90.0)
        assert rm.check_stop_loss(pos, current_price=85.0) is True

    def test_long_does_not_hit(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BULLISH, stop_loss=90.0)
        assert rm.check_stop_loss(pos, current_price=95.0) is False

    def test_short_hits_sl(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BEARISH, stop_loss=110.0)
        assert rm.check_stop_loss(pos, current_price=115.0) is True

    def test_short_does_not_hit(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BEARISH, stop_loss=110.0)
        assert rm.check_stop_loss(pos, current_price=105.0) is False

    def test_boundary_equals_sl_triggers(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BULLISH, stop_loss=90.0)
        assert rm.check_stop_loss(pos, current_price=90.0) is True

    def test_boundary_equals_sl_short_triggers(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BEARISH, stop_loss=110.0)
        assert rm.check_stop_loss(pos, current_price=110.0) is True


# ── check_take_profit ────────────────────────────────────────


class TestCheckTakeProfit:
    def test_long_hits_tp(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BULLISH, take_profit=115.0)
        assert rm.check_take_profit(pos, current_price=120.0) is True

    def test_long_does_not_hit(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BULLISH, take_profit=115.0)
        assert rm.check_take_profit(pos, current_price=110.0) is False

    def test_tp_none_returns_false(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BULLISH, take_profit=None)
        assert rm.check_take_profit(pos, current_price=999.0) is False

    def test_short_hits_tp(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BEARISH, take_profit=85.0)
        assert rm.check_take_profit(pos, current_price=80.0) is True

    def test_short_does_not_hit_tp(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BEARISH, take_profit=85.0)
        assert rm.check_take_profit(pos, current_price=90.0) is False


# ── check_time_limit ─────────────────────────────────────────


class TestCheckTimeLimit:
    def test_exceeded(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position()
        # Force entry_time to 20 minutes ago (limit is 15 min)
        pos.entry_time = datetime.now() - timedelta(minutes=20)
        assert rm.check_time_limit(pos) is True

    def test_not_exceeded(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position()
        # entry_time is datetime.now() from the fixture, well under 15 min
        assert rm.check_time_limit(pos) is False


# ── check_trailing_stop ──────────────────────────────────────


class TestCheckTrailingStop:
    def test_long_price_drops_past_threshold(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        # trailing_stop_pct = 1.5; peak = 200; threshold = 200 * 0.985 = 197
        pos = make_position(direction=Sentiment.BULLISH, entry_price=180.0, peak_price=200.0)
        assert rm.check_trailing_stop(pos, current_price=196.0) is True

    def test_long_price_near_peak(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BULLISH, entry_price=180.0, peak_price=200.0)
        # 199 > 200 * 0.985 = 197 -> no trigger
        assert rm.check_trailing_stop(pos, current_price=199.0) is False

    def test_short_price_rises_past_threshold(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        # Short peak (lowest) = 80; threshold = 80 * 1.015 = 81.2
        pos = make_position(direction=Sentiment.BEARISH, entry_price=90.0, peak_price=80.0)
        assert rm.check_trailing_stop(pos, current_price=82.0) is True

    def test_short_price_near_peak(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BEARISH, entry_price=90.0, peak_price=80.0)
        assert rm.check_trailing_stop(pos, current_price=80.5) is False

    def test_peak_price_zero_returns_false(self, mock_config):
        rm = _make_rm(mock_config)
        pos = Position(
            figi="BBG004730N88",
            ticker="SBER",
            direction=Sentiment.BULLISH,
            entry_price=100.0,
            quantity=10,
            entry_time=datetime.now(),
            stop_loss=90.0,
            take_profit=115.0,
            atr=5.0,
            peak_price=0.0,
            state=PositionState.ACTIVE,
        )
        # Override the __post_init__ default so peak_price stays 0
        pos.peak_price = 0.0
        assert rm.check_trailing_stop(pos, current_price=50.0) is False


# ── calculate_pnl ────────────────────────────────────────────


class TestCalculatePnl:
    def test_long_profit(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BULLISH, entry_price=100.0, quantity=10)
        assert rm.calculate_pnl(pos, exit_price=110.0) == pytest.approx(100.0)

    def test_long_loss(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BULLISH, entry_price=100.0, quantity=10)
        assert rm.calculate_pnl(pos, exit_price=95.0) == pytest.approx(-50.0)

    def test_short_profit(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BEARISH, entry_price=100.0, quantity=10)
        assert rm.calculate_pnl(pos, exit_price=90.0) == pytest.approx(100.0)

    def test_short_loss(self, mock_config, make_position):
        rm = _make_rm(mock_config)
        pos = make_position(direction=Sentiment.BEARISH, entry_price=100.0, quantity=10)
        assert rm.calculate_pnl(pos, exit_price=105.0) == pytest.approx(-50.0)


# ── can_open_position ────────────────────────────────────────


class TestCanOpenPosition:
    def test_normal_ok(self, mock_config):
        rm = _make_rm(mock_config)
        ok, reason = rm.can_open_position(price=100, quantity=10, current_positions=0)
        assert ok is True
        assert reason == "OK"

    def test_max_positions_exceeded(self, mock_config):
        rm = _make_rm(mock_config)
        ok, reason = rm.can_open_position(price=100, quantity=10, current_positions=3)
        assert ok is False
        assert "Max positions" in reason

    def test_daily_loss_exceeded(self, mock_config):
        rm = _make_rm(mock_config)
        rm._daily_pnl = -10000.0
        ok, reason = rm.can_open_position(price=100, quantity=10, current_positions=0)
        assert ok is False
        assert "Daily loss" in reason

    def test_position_size_exceeded(self, mock_config):
        rm = _make_rm(mock_config)
        # max_position_rub = 50000; 1000 * 100 = 100000 > 50000
        ok, reason = rm.can_open_position(price=1000, quantity=100, current_positions=0)
        assert ok is False
        assert "Position size" in reason


# ── record_trade + get_daily_stats ───────────────────────────


class TestDailyStats:
    def test_record_trade_updates_pnl(self, mock_config):
        rm = _make_rm(mock_config)
        rm.record_trade(500.0)
        rm.record_trade(-200.0)
        stats = rm.get_daily_stats()
        assert stats["daily_pnl"] == pytest.approx(300.0)
        assert stats["daily_trades"] == 2

    def test_remaining_loss_limit(self, mock_config):
        rm = _make_rm(mock_config)
        rm.record_trade(-3000.0)
        stats = rm.get_daily_stats()
        # remaining = 10000 + (-3000) = 7000
        assert stats["remaining_loss_limit"] == pytest.approx(7000.0)


# ── calculate_atr (async, mocked client) ────────────────────


class TestCalculateAtr:
    async def test_atr_with_known_candles(self, mock_config):
        client = MagicMock()
        rm = RiskManager(client=client, config=mock_config)

        # Build 16 candles (need period+1 = 15 for full ATR with period=14).
        # Simple candles: high=H, low=L, close=C.
        # TR for each = max(H-L, |H-prev_close|, |L-prev_close|)
        # Use uniform candles: H=105, L=95, C=100 => TR = 10 for all.
        candles = []
        for _ in range(16):
            candles.append(_make_candle(open_=100, high=105, low=95, close=100))

        response = MagicMock()
        response.candles = candles
        client.market_data.get_candles.return_value = response

        atr = await rm.calculate_atr("BBG004730N88", period=14)
        # All TRs are 10, so ATR = 10
        assert atr == pytest.approx(10.0)

    async def test_atr_no_candles_returns_0(self, mock_config):
        client = MagicMock()
        rm = RiskManager(client=client, config=mock_config)

        response = MagicMock()
        response.candles = []
        client.market_data.get_candles.return_value = response

        atr = await rm.calculate_atr("BBG004730N88")
        assert atr == 0.0

    async def test_atr_insufficient_candles_uses_fallback(self, mock_config):
        client = MagicMock()
        rm = RiskManager(client=client, config=mock_config)

        # Only 3 candles (< period+1 = 15), fallback = last high - low
        candles = [
            _make_candle(100, 108, 96, 102),
            _make_candle(102, 110, 98, 105),
            _make_candle(105, 112, 100, 107),  # last: 112 - 100 = 12
        ]
        response = MagicMock()
        response.candles = candles
        client.market_data.get_candles.return_value = response

        atr = await rm.calculate_atr("BBG004730N88", period=14)
        assert atr == pytest.approx(12.0)

    async def test_atr_api_error_returns_0(self, mock_config):
        client = MagicMock()
        rm = RiskManager(client=client, config=mock_config)
        client.market_data.get_candles.side_effect = RuntimeError("API down")

        atr = await rm.calculate_atr("BBG004730N88")
        assert atr == 0.0


# ── check_momentum (async, mocked client) ───────────────────


class TestCheckMomentum:
    async def test_bearish_majority(self, mock_config):
        """80%+ bearish candles -> BEARISH."""
        client = MagicMock()
        rm = RiskManager(client=client, config=mock_config)

        # 5 candles, 4 bearish (close < open), 1 bullish -> 80% bearish
        candles = [
            _make_candle(100, 101, 98, 99),   # bearish
            _make_candle(100, 102, 97, 98),   # bearish
            _make_candle(100, 103, 96, 97),   # bearish
            _make_candle(100, 104, 95, 96),   # bearish
            _make_candle(100, 105, 99, 101),  # bullish
        ]
        response = MagicMock()
        response.candles = candles
        client.market_data.get_candles.return_value = response

        result = await rm.check_momentum("BBG004730N88")
        assert result == Sentiment.BEARISH

    async def test_bullish_majority(self, mock_config):
        """80%+ bullish candles -> BULLISH."""
        client = MagicMock()
        rm = RiskManager(client=client, config=mock_config)

        # 5 candles, 4 bullish (close > open), 1 bearish -> 80% bullish
        candles = [
            _make_candle(100, 105, 99, 103),  # bullish
            _make_candle(100, 106, 98, 104),  # bullish
            _make_candle(100, 107, 97, 105),  # bullish
            _make_candle(100, 108, 96, 106),  # bullish
            _make_candle(100, 101, 95, 99),   # bearish
        ]
        response = MagicMock()
        response.candles = candles
        client.market_data.get_candles.return_value = response

        result = await rm.check_momentum("BBG004730N88")
        assert result == Sentiment.BULLISH

    async def test_mixed_returns_neutral(self, mock_config):
        """No clear majority -> NEUTRAL."""
        client = MagicMock()
        rm = RiskManager(client=client, config=mock_config)

        # 5 candles: 3 bearish, 2 bullish -> 60% bearish < 80%
        candles = [
            _make_candle(100, 101, 98, 99),   # bearish
            _make_candle(100, 102, 97, 98),   # bearish
            _make_candle(100, 103, 96, 97),   # bearish
            _make_candle(100, 105, 99, 102),  # bullish
            _make_candle(100, 106, 98, 103),  # bullish
        ]
        response = MagicMock()
        response.candles = candles
        client.market_data.get_candles.return_value = response

        result = await rm.check_momentum("BBG004730N88")
        assert result == Sentiment.NEUTRAL

    async def test_insufficient_candles_returns_neutral(self, mock_config):
        """Fewer than 3 candles -> NEUTRAL."""
        client = MagicMock()
        rm = RiskManager(client=client, config=mock_config)

        candles = [
            _make_candle(100, 105, 95, 99),
            _make_candle(100, 106, 94, 98),
        ]
        response = MagicMock()
        response.candles = candles
        client.market_data.get_candles.return_value = response

        result = await rm.check_momentum("BBG004730N88")
        assert result == Sentiment.NEUTRAL

    async def test_api_error_returns_neutral(self, mock_config):
        client = MagicMock()
        rm = RiskManager(client=client, config=mock_config)
        client.market_data.get_candles.side_effect = RuntimeError("timeout")

        result = await rm.check_momentum("BBG004730N88")
        assert result == Sentiment.NEUTRAL
