"""
Walk-forward position simulator on historical candle data.

Replicates RiskManager SL/TP/trailing/time-limit logic without
needing a Tinkoff Client — just candle dicts and TradingConfig.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from bot.config import TradingConfig
from bot.models import Sentiment


@dataclass
class TradeResult:
    """Outcome of one simulated trade."""

    ticker: str
    direction: Sentiment
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    exit_reason: str  # STOP_LOSS, TAKE_PROFIT, TIME_LIMIT, TRAILING_STOP, END_OF_DATA
    pnl: float
    duration_sec: float
    atr: float
    quantity: int = 1


class PositionSimulator:
    """Simulates a single position's lifecycle on candle data."""

    def __init__(self, config: TradingConfig):
        self.config = config

    def simulate(
        self,
        candles: list[dict],
        direction: Sentiment,
        entry_time: datetime,
        atr: float,
        ticker: str = "",
        quantity: int = 1,
    ) -> Optional[TradeResult]:
        """Simulate one position.

        Args:
            candles: List of {time, open, high, low, close} dicts, sorted by time.
            direction: BULLISH (long) or BEARISH (short).
            entry_time: Desired entry timestamp.
            atr: Pre-computed ATR for SL/TP calculation.
            ticker: For labeling the result.
            quantity: Number of shares (for PnL calculation).

        Returns:
            TradeResult or None if no entry candle found.
        """
        if not candles or direction == Sentiment.NEUTRAL:
            return None

        # ── Find entry candle (first at or after entry_time) ──
        entry_idx = None
        for i, c in enumerate(candles):
            if c["time"] >= entry_time:
                entry_idx = i
                break
        if entry_idx is None:
            return None

        entry_price = candles[entry_idx]["open"]

        # ── Compute SL / TP ───────────────────────────────────
        sl_mult = self.config.stop_loss_atr_mult
        tp_mult = self.config.take_profit_atr_mult
        trailing_pct = self.config.trailing_stop_pct / 100.0
        max_time_sec = self.config.max_position_time_min * 60

        if direction == Sentiment.BULLISH:
            stop_loss = entry_price - atr * sl_mult
            take_profit = entry_price + atr * tp_mult
        else:
            stop_loss = entry_price + atr * sl_mult
            take_profit = entry_price - atr * tp_mult

        peak_price = entry_price
        is_long = direction == Sentiment.BULLISH

        # ── Walk forward candle by candle ─────────────────────
        for c in candles[entry_idx + 1 :]:
            candle_time: datetime = c["time"]
            elapsed = (candle_time - candles[entry_idx]["time"]).total_seconds()

            # --- SL check (worst-case intra-candle price) ---
            sl_price = c["low"] if is_long else c["high"]
            if is_long and sl_price <= stop_loss:
                return self._result(
                    ticker, direction, entry_price, stop_loss,
                    candles[entry_idx]["time"], candle_time,
                    "STOP_LOSS", atr, quantity,
                )
            if not is_long and sl_price >= stop_loss:
                return self._result(
                    ticker, direction, entry_price, stop_loss,
                    candles[entry_idx]["time"], candle_time,
                    "STOP_LOSS", atr, quantity,
                )

            # --- TP check (best-case intra-candle price) ---
            tp_price = c["high"] if is_long else c["low"]
            if is_long and tp_price >= take_profit:
                return self._result(
                    ticker, direction, entry_price, take_profit,
                    candles[entry_idx]["time"], candle_time,
                    "TAKE_PROFIT", atr, quantity,
                )
            if not is_long and tp_price <= take_profit:
                return self._result(
                    ticker, direction, entry_price, take_profit,
                    candles[entry_idx]["time"], candle_time,
                    "TAKE_PROFIT", atr, quantity,
                )

            # --- Time limit ---
            if elapsed >= max_time_sec:
                return self._result(
                    ticker, direction, entry_price, c["close"],
                    candles[entry_idx]["time"], candle_time,
                    "TIME_LIMIT", atr, quantity,
                )

            # --- Update peak price ---
            if is_long:
                peak_price = max(peak_price, c["high"])
            else:
                peak_price = min(peak_price, c["low"])

            # --- Trailing stop ---
            if is_long:
                threshold = peak_price * (1 - trailing_pct)
                if c["close"] <= threshold:
                    return self._result(
                        ticker, direction, entry_price, c["close"],
                        candles[entry_idx]["time"], candle_time,
                        "TRAILING_STOP", atr, quantity,
                    )
            else:
                threshold = peak_price * (1 + trailing_pct)
                if c["close"] >= threshold:
                    return self._result(
                        ticker, direction, entry_price, c["close"],
                        candles[entry_idx]["time"], candle_time,
                        "TRAILING_STOP", atr, quantity,
                    )

        # ── No exit triggered — force close at last candle ────
        last = candles[-1]
        return self._result(
            ticker, direction, entry_price, last["close"],
            candles[entry_idx]["time"], last["time"],
            "END_OF_DATA", atr, quantity,
        )

    @staticmethod
    def _result(
        ticker: str,
        direction: Sentiment,
        entry_price: float,
        exit_price: float,
        entry_time: datetime,
        exit_time: datetime,
        reason: str,
        atr: float,
        quantity: int,
    ) -> TradeResult:
        if direction == Sentiment.BULLISH:
            pnl = (exit_price - entry_price) * quantity
        elif direction == Sentiment.BEARISH:
            pnl = (entry_price - exit_price) * quantity
        else:
            pnl = 0.0

        duration = (exit_time - entry_time).total_seconds()

        return TradeResult(
            ticker=ticker,
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_price,
            entry_time=entry_time,
            exit_time=exit_time,
            exit_reason=reason,
            pnl=pnl,
            duration_sec=duration,
            atr=atr,
            quantity=quantity,
        )
