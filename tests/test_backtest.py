"""Tests for the backtest module — simulator, data_loader, report."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from backtest.data_loader import (
    CandleCache,
    calculate_atr_from_candles,
    normalize_candle_dt,
    normalize_signal_dt,
    _load_csv,
    _save_csv,
)
from backtest.report import BacktestReport
from backtest.runner import BacktestRunner, Signal
from backtest.simulator import PositionSimulator, TradeResult
from bot.config import TradingConfig
from bot.models import Sentiment


# ── Helpers ───────────────────────────────────────────────────


def _candles(
    start: datetime,
    prices: list[tuple[float, float, float, float]],
    interval_min: int = 1,
) -> list[dict]:
    """Build candle dicts from (open, high, low, close) tuples."""
    result = []
    for i, (o, h, l, c) in enumerate(prices):
        result.append({
            "time": start + timedelta(minutes=i * interval_min),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 100,
        })
    return result


T0 = datetime(2026, 4, 1, 10, 0, 0)


# ══════════════════════════════════════════════════════════════
# calculate_atr_from_candles
# ══════════════════════════════════════════════════════════════


class TestATRFromCandles:
    def test_known_values(self):
        # 3 candles: TR[1] = max(10, |110-100|, |90-100|) = 20 (high-low wins)
        #            TR[2] = max(10, |115-105|, |95-105|) = 10
        candles = _candles(T0, [
            (100, 110, 90, 100),    # candle 0
            (100, 110, 100, 105),   # candle 1: TR = max(10, 5, 5) = 10
            (105, 115, 95, 110),    # candle 2: TR = max(20, 10, 10) = 20
        ])
        atr = calculate_atr_from_candles(candles, period=14)
        assert atr == pytest.approx(15.0)  # (10 + 20) / 2

    def test_single_candle_fallback(self):
        candles = _candles(T0, [(100, 110, 90, 100)])
        atr = calculate_atr_from_candles(candles)
        assert atr == pytest.approx(20.0)  # high - low

    def test_empty_returns_zero(self):
        assert calculate_atr_from_candles([]) == 0.0

    def test_period_limits_window(self):
        # 5 candles, period=2 → only last 2 TRs used
        candles = _candles(T0, [
            (100, 110, 90, 100),
            (100, 100, 100, 100),  # TR = max(0, 0, 0) = 0
            (100, 100, 100, 100),  # TR = 0
            (100, 100, 100, 100),  # TR = 0
            (100, 120, 80, 100),   # TR = max(40, 20, 20) = 40
        ])
        atr = calculate_atr_from_candles(candles, period=2)
        assert atr == pytest.approx(20.0)  # (0 + 40) / 2


# ══════════════════════════════════════════════════════════════
# PositionSimulator
# ══════════════════════════════════════════════════════════════


class TestSimulator:
    @pytest.fixture()
    def sim(self):
        return PositionSimulator(TradingConfig(
            stop_loss_atr_mult=2.0,
            take_profit_atr_mult=3.0,
            max_position_time_min=15,
            trailing_stop_pct=1.5,
        ))

    def test_long_hits_stop_loss(self, sim):
        # Entry 100, ATR=5, SL=90. Candle drops to 89.
        candles = _candles(T0, [
            (100, 101, 99, 100),    # entry candle
            (100, 101, 99, 100),    # normal
            (100, 101, 89, 95),     # low hits SL
        ])
        r = sim.simulate(candles, Sentiment.BULLISH, T0, atr=5.0, ticker="TEST")
        assert r.exit_reason == "STOP_LOSS"
        assert r.exit_price == 90.0
        assert r.pnl < 0

    def test_long_hits_take_profit(self, sim):
        # Entry 100, ATR=5, TP=115. Candle goes to 116.
        candles = _candles(T0, [
            (100, 101, 99, 100),
            (100, 116, 99, 110),    # high hits TP
        ])
        r = sim.simulate(candles, Sentiment.BULLISH, T0, atr=5.0, ticker="TEST")
        assert r.exit_reason == "TAKE_PROFIT"
        assert r.exit_price == 115.0
        assert r.pnl > 0

    def test_time_limit_exit(self, sim):
        # 16 candles (16 min), time limit=15 min
        prices = [(100, 101, 99, 100)] * 17
        candles = _candles(T0, prices)
        r = sim.simulate(candles, Sentiment.BULLISH, T0, atr=5.0, ticker="TEST")
        assert r.exit_reason == "TIME_LIMIT"

    def test_trailing_stop_long(self, sim):
        # Price rises to 110, then drops by >1.5% (1.65) → exit
        candles = _candles(T0, [
            (100, 101, 99, 100),    # entry
            (100, 110, 100, 110),   # peak=110
            (110, 110, 100, 108),   # 1.8% drop from 110 → threshold=108.35, close=108 < 108.35
        ])
        r = sim.simulate(candles, Sentiment.BULLISH, T0, atr=5.0, ticker="TEST")
        assert r.exit_reason == "TRAILING_STOP"
        assert r.exit_price == 108.0

    def test_short_hits_stop_loss(self, sim):
        # Short entry 100, ATR=5, SL=110. Candle goes to 111.
        candles = _candles(T0, [
            (100, 101, 99, 100),
            (100, 111, 99, 105),    # high hits SL
        ])
        r = sim.simulate(candles, Sentiment.BEARISH, T0, atr=5.0, ticker="TEST")
        assert r.exit_reason == "STOP_LOSS"
        assert r.exit_price == 110.0
        assert r.pnl < 0

    def test_short_hits_take_profit(self, sim):
        # Short entry 100, ATR=5, TP=85. Candle drops to 84.
        candles = _candles(T0, [
            (100, 101, 99, 100),
            (100, 101, 84, 90),     # low hits TP
        ])
        r = sim.simulate(candles, Sentiment.BEARISH, T0, atr=5.0, ticker="TEST")
        assert r.exit_reason == "TAKE_PROFIT"
        assert r.exit_price == 85.0
        assert r.pnl > 0

    def test_end_of_data_exit(self, sim):
        # Only 2 candles, no exit triggered
        candles = _candles(T0, [
            (100, 101, 99, 100),
            (100, 101, 99, 100),
        ])
        r = sim.simulate(candles, Sentiment.BULLISH, T0, atr=5.0, ticker="TEST")
        assert r.exit_reason == "END_OF_DATA"
        assert r.exit_price == 100.0

    def test_entry_at_correct_candle(self, sim):
        # Entry time is at T0+2min, should skip first 2 candles
        entry_time = T0 + timedelta(minutes=2)
        candles = _candles(T0, [
            (90, 91, 89, 90),       # t+0 — skipped
            (95, 96, 94, 95),       # t+1 — skipped
            (100, 101, 99, 100),    # t+2 — entry candle (open=100)
            (100, 101, 99, 100),    # t+3
        ])
        r = sim.simulate(candles, Sentiment.BULLISH, entry_time, atr=5.0, ticker="TEST")
        assert r.entry_price == 100.0  # not 90

    def test_neutral_returns_none(self, sim):
        candles = _candles(T0, [(100, 101, 99, 100)])
        r = sim.simulate(candles, Sentiment.NEUTRAL, T0, atr=5.0)
        assert r is None

    def test_empty_candles_returns_none(self, sim):
        r = sim.simulate([], Sentiment.BULLISH, T0, atr=5.0)
        assert r is None

    def test_pnl_calculation_long(self, sim):
        # Entry 100, exit at TP 115, qty=10
        candles = _candles(T0, [
            (100, 101, 99, 100),
            (100, 116, 99, 110),
        ])
        r = sim.simulate(candles, Sentiment.BULLISH, T0, atr=5.0, quantity=10)
        assert r.pnl == pytest.approx(150.0)  # (115 - 100) * 10

    def test_pnl_calculation_short(self, sim):
        # Short entry 100, exit at TP 85, qty=10
        candles = _candles(T0, [
            (100, 101, 99, 100),
            (100, 101, 84, 90),
        ])
        r = sim.simulate(candles, Sentiment.BEARISH, T0, atr=5.0, quantity=10)
        assert r.pnl == pytest.approx(150.0)  # (100 - 85) * 10


# ══════════════════════════════════════════════════════════════
# BacktestReport
# ══════════════════════════════════════════════════════════════


class TestReport:
    def _results(self, pnls: list[float]) -> list[TradeResult]:
        return [
            TradeResult(
                ticker="TEST", direction=Sentiment.BULLISH,
                entry_price=100, exit_price=100 + p,
                entry_time=T0, exit_time=T0 + timedelta(minutes=5),
                exit_reason="TAKE_PROFIT" if p > 0 else "STOP_LOSS",
                pnl=p, duration_sec=300, atr=5.0,
            )
            for p in pnls
        ]

    def test_win_rate(self):
        report = BacktestReport(self._results([10, -5, 20, -3]))
        s = report.summary()
        assert s["win_rate"] == 50.0
        assert s["wins"] == 2
        assert s["losses"] == 2

    def test_total_pnl(self):
        report = BacktestReport(self._results([10, -5, 20]))
        s = report.summary()
        assert s["total_pnl"] == pytest.approx(25.0)

    def test_max_drawdown(self):
        # Equity: 10, 5, 25 → peak=10, dd=5, then peak=25, no further dd
        report = BacktestReport(self._results([10, -5, 20]))
        s = report.summary()
        assert s["max_drawdown"] == pytest.approx(5.0)

    def test_empty_results(self):
        report = BacktestReport([])
        s = report.summary()
        assert s["total"] == 0
        assert s["win_rate"] == 0.0

    def test_profit_factor(self):
        report = BacktestReport(self._results([100, -25, -25]))
        s = report.summary()
        assert s["profit_factor"] == pytest.approx(2.0)  # 100 / 50

    def test_equity_curve(self):
        report = BacktestReport(self._results([10, -5, 20]))
        curve = report.equity_curve()
        assert len(curve) == 3
        assert curve[0][1] == pytest.approx(10.0)
        assert curve[1][1] == pytest.approx(5.0)
        assert curve[2][1] == pytest.approx(25.0)

    def test_by_exit_reason(self):
        report = BacktestReport(self._results([10, -5]))
        reasons = report.by_exit_reason()
        assert "TAKE_PROFIT" in reasons
        assert "STOP_LOSS" in reasons
        assert reasons["TAKE_PROFIT"]["count"] == 1


# ══════════════════════════════════════════════════════════════
# CandleCache (CSV round-trip)
# ══════════════════════════════════════════════════════════════


class TestCandleCache:
    def test_csv_roundtrip(self, tmp_path):
        candles = _candles(T0, [(100, 110, 90, 105), (105, 115, 95, 110)])
        path = tmp_path / "test.csv"
        _save_csv(path, candles)
        loaded = _load_csv(path)
        assert len(loaded) == 2
        assert loaded[0]["open"] == 100.0
        assert loaded[1]["close"] == 110.0
        assert isinstance(loaded[0]["time"], datetime)

    def test_cache_path(self, tmp_path):
        from datetime import date
        cache = CandleCache(client=None, cache_dir=tmp_path)
        p = cache._cache_path("BBG004730N88", date(2026, 4, 1))
        assert "BBG004730N88_2026-04-01.csv" in str(p)

    def test_get_candles_from_cache(self, tmp_path):
        from datetime import date
        cache = CandleCache(client=None, cache_dir=tmp_path)
        candles = _candles(T0, [(100, 110, 90, 105)])
        _save_csv(cache._cache_path("BBG004730N88", date(2026, 4, 1)), candles)

        import asyncio
        loaded = asyncio.run(cache.get_candles("BBG004730N88", date(2026, 4, 1)))
        assert len(loaded) == 1
        assert loaded[0]["open"] == 100.0


# ══════════════════════════════════════════════════════════════
# Signal CSV loading
# ══════════════════════════════════════════════════════════════


class TestSignalCSV:
    def test_load_signals(self, tmp_path):
        csv_content = "ticker,direction,timestamp\nSBER,bullish,2026-04-01T10:30:00\nGAZP,bearish,2026-04-01T11:00:00\n"
        csv_path = tmp_path / "signals.csv"
        csv_path.write_text(csv_content)

        signals = BacktestRunner.load_signals_csv(csv_path)
        assert len(signals) == 2
        assert signals[0].ticker == "SBER"
        assert signals[0].direction == Sentiment.BULLISH
        assert signals[1].ticker == "GAZP"
        assert signals[1].direction == Sentiment.BEARISH

    def test_skip_invalid_direction(self, tmp_path):
        csv_content = "ticker,direction,timestamp\nSBER,neutral,2026-04-01T10:30:00\n"
        csv_path = tmp_path / "signals.csv"
        csv_path.write_text(csv_content)

        signals = BacktestRunner.load_signals_csv(csv_path)
        assert len(signals) == 0


# ══════════════════════════════════════════════════════════════
# Timezone normalization
# ══════════════════════════════════════════════════════════════


class TestTimezoneNormalization:
    def test_signal_naive_treated_as_msk(self):
        local = datetime(2026, 1, 15, 10, 30, 0)
        utc = normalize_signal_dt(local)
        assert utc == datetime(2026, 1, 15, 7, 30, 0)

    def test_candle_naive_left_as_utc(self):
        ts = datetime(2026, 1, 15, 7, 30, 0)
        assert normalize_candle_dt(ts) == ts
