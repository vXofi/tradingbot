"""
Position Tracker — отслеживание открытых позиций.

Unified exit path: every exit condition produces an ExitSignal.
- should_reverse=False  (SL/TP/time) → closed locally, callback for logging
- should_reverse=True   (trailing/momentum) → NOT closed here, callback does close + reversal
"""

import asyncio
import time as _time
from datetime import datetime
from typing import Awaitable, Callable, Optional

from t_tech.invest import Client

from ..config import Config
from ..models import ExitSignal, Position, PositionState, Sentiment
from .risk_manager import RiskManager


class PositionTracker:
    """Отслеживание и мониторинг открытых позиций."""

    def __init__(
        self,
        client: Client,
        config: Config,
        risk_manager: RiskManager,
        trade_db=None,
        rate_limiter=None,
    ):
        self.client = client
        self.config = config
        self.risk_manager = risk_manager
        self._trade_db = trade_db
        self._rate_limiter = rate_limiter

        self._positions: dict[str, Position] = {}
        self._positions_lock = asyncio.Lock()
        self._history: list[Position] = []

        # Unified exit callback (set via on_exit())
        self._on_exit: Optional[Callable[[ExitSignal], Awaitable]] = None

        # Momentum check throttle: figi → last check monotonic time
        self._last_momentum_check: dict[str, float] = {}

        self._monitoring = False

    # ── public API ──────────────────────────────────────────

    def load_history(self):
        """Load persisted trade history from database."""
        if self._trade_db is None:
            return
        loaded = self._trade_db.load_history()
        self._history = loaded + self._history

    def on_exit(self, callback: Callable[[ExitSignal], Awaitable]):
        """Register the single unified exit callback."""
        self._on_exit = callback

    async def add_position(self, position: Position):
        """Добавить позицию для отслеживания."""
        async with self._positions_lock:
            self._positions[position.figi] = position
        tp_str = f"{position.take_profit:.2f}" if position.take_profit else "N/A"
        print(
            f"📈 Position opened: {position.ticker} "
            f"{position.direction.value} @ {position.entry_price:.2f}"
        )
        print(f"   SL: {position.stop_loss:.2f} | TP: {tp_str}")

    def get_position(self, figi: str) -> Optional[Position]:
        return self._positions.get(figi)

    def get_all_positions(self) -> list[Position]:
        return list(self._positions.values())

    def get_positions_count(self) -> int:
        return len(self._positions)

    def has_position(self, figi: str) -> bool:
        return figi in self._positions

    # ── close ───────────────────────────────────────────────

    async def close_position(
        self,
        figi: str,
        exit_price: float,
        reason: str,
    ) -> Optional[Position]:
        """
        Закрыть позицию (убрать из отслеживания, записать PnL).

        Returns:
            Закрытая позиция или None.
        """
        async with self._positions_lock:
            position = self._positions.pop(figi, None)
        if position is None:
            return None

        position.is_open = False
        position.state = PositionState.CLOSED
        position.exit_price = exit_price
        position.exit_time = datetime.now()
        position.exit_reason = reason
        position.pnl = self.risk_manager.calculate_pnl(position, exit_price)

        self.risk_manager.record_trade(position.pnl)
        self._history.append(position)

        if self._trade_db is not None:
            try:
                self._trade_db.save_position(position)
            except Exception as e:
                print(f"Trade DB save error: {e}")

        pnl_emoji = "✅" if position.pnl >= 0 else "❌"
        print(f"{pnl_emoji} Position closed: {position.ticker} @ {exit_price:.2f}")
        print(f"   Reason: {reason} | PnL: {position.pnl:+.2f} RUB")

        return position

    # ── price helper ────────────────────────────────────────

    async def _get_current_price(self, figi: str) -> Optional[float]:
        try:
            if self._rate_limiter:
                await self._rate_limiter.acquire()
            response = self.client.market_data.get_last_prices(figi=[figi])
            if response.last_prices:
                p = response.last_prices[0].price
                return p.units + p.nano / 1e9
        except Exception as e:
            print(f"Price fetch error for {figi}: {e}")
        return None

    # ── peak tracking (owned by tracker, not RiskManager) ──

    @staticmethod
    def _update_peak(position: Position, current_price: float):
        if position.direction == Sentiment.BULLISH:
            position.peak_price = max(position.peak_price, current_price)
        elif position.direction == Sentiment.BEARISH:
            if position.peak_price <= 0:
                position.peak_price = current_price
            else:
                position.peak_price = min(position.peak_price, current_price)

    # ── emit helper ─────────────────────────────────────────

    async def _emit_exit(self, signal: ExitSignal):
        if self._on_exit is None:
            return
        try:
            await self._on_exit(signal)
        except Exception as e:
            print(f"Exit callback error: {e}")

    # ── monitor loop ────────────────────────────────────────

    async def monitor_loop(self, interval_sec: float | None = None):
        """
        Бесконечный цикл мониторинга позиций.

        Порядок проверок для каждой ACTIVE позиции:
        1. Static SL / TP / Time → close locally, emit ExitSignal(should_reverse=False)
        2. Update peak_price
        3. Trailing stop        → emit ExitSignal(should_reverse=True), no local close
        4. Momentum (throttled) → emit ExitSignal(should_reverse=True), no local close
        """
        interval_sec = interval_sec or self.config.trading.monitor_interval_sec
        self._monitoring = True
        print(f"🔄 Position monitoring started (interval={interval_sec}s)")

        try:
            while self._monitoring:
                async with self._positions_lock:
                    figis = list(self._positions.keys())

                for figi in figis:
                    position = self._positions.get(figi)
                    if position is None or position.state != PositionState.ACTIVE:
                        continue

                    price = await self._get_current_price(figi)
                    if price is None:
                        continue

                    # 1. Static exits (no reversal)
                    static_reason = self._check_static_exit(position, price)
                    if static_reason:
                        await self.close_position(figi, price, static_reason)
                        await self._emit_exit(ExitSignal(
                            figi=figi,
                            ticker=position.ticker,
                            reason=static_reason,
                            current_price=price,
                            should_reverse=False,
                            position=position,
                        ))
                        continue

                    # 2. Peak tracking
                    self._update_peak(position, price)

                    # 3. Trailing stop
                    if self.risk_manager.check_trailing_stop(position, price):
                        await self._emit_exit(ExitSignal(
                            figi=figi,
                            ticker=position.ticker,
                            reason="TRAILING_STOP",
                            current_price=price,
                            should_reverse=True,
                            position=position,
                        ))
                        continue

                    # 4. Momentum (throttled)
                    await self._check_momentum_throttled(position, price)

                await asyncio.sleep(interval_sec)

        except asyncio.CancelledError:
            print("🛑 Position monitoring stopped")
        finally:
            self._monitoring = False

    def _check_static_exit(
        self, position: Position, current_price: float
    ) -> Optional[str]:
        """Return exit reason for SL/TP/time, or None."""
        if self.risk_manager.check_stop_loss(position, current_price):
            return "STOP_LOSS"
        if self.risk_manager.check_take_profit(position, current_price):
            return "TAKE_PROFIT"
        if self.risk_manager.check_time_limit(position):
            return "TIME_LIMIT"
        return None

    async def _check_momentum_throttled(
        self, position: Position, current_price: float
    ):
        interval = self.config.trading.momentum_check_interval_sec
        now = _time.monotonic()
        last = self._last_momentum_check.get(position.figi, 0.0)

        if now - last < interval:
            return

        self._last_momentum_check[position.figi] = now

        momentum = await self.risk_manager.check_momentum(position.figi)
        if momentum == Sentiment.NEUTRAL:
            return

        opposite = (
            (position.direction == Sentiment.BULLISH and momentum == Sentiment.BEARISH)
            or (position.direction == Sentiment.BEARISH and momentum == Sentiment.BULLISH)
        )
        if opposite:
            await self._emit_exit(ExitSignal(
                figi=position.figi,
                ticker=position.ticker,
                reason="MOMENTUM",
                current_price=current_price,
                should_reverse=True,
                position=position,
            ))

    def stop_monitoring(self):
        self._monitoring = False

    # ── stats / history ─────────────────────────────────────

    def get_history(self, limit: int = 100) -> list[Position]:
        return self._history[-limit:]

    def get_stats(self) -> dict:
        if not self._history:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "avg_pnl": 0.0,
                "best_trade": 0.0,
                "worst_trade": 0.0,
            }

        pnls = [p.pnl for p in self._history if p.pnl is not None]
        wins = [p for p in pnls if p > 0]

        return {
            "total_trades": len(self._history),
            "win_rate": len(wins) / len(pnls) if pnls else 0.0,
            "total_pnl": sum(pnls),
            "avg_pnl": sum(pnls) / len(pnls) if pnls else 0.0,
            "best_trade": max(pnls) if pnls else 0.0,
            "worst_trade": min(pnls) if pnls else 0.0,
            "open_positions": len(self._positions),
        }

    def print_status(self):
        print("\n" + "=" * 50)
        print("POSITION STATUS")
        print("=" * 50)

        if not self._positions:
            print("No open positions")
        else:
            for figi, pos in self._positions.items():
                elapsed = (datetime.now() - pos.entry_time).total_seconds() / 60
                print(f"\n{pos.ticker} ({pos.direction.value})")
                tp_str = f"{pos.take_profit:.2f}" if pos.take_profit else "N/A"
                print(f"  Entry: {pos.entry_price:.2f}  Peak: {pos.peak_price:.2f}")
                print(f"  SL: {pos.stop_loss:.2f} | TP: {tp_str}")
                print(f"  State: {pos.state.value} | Time: {elapsed:.1f} min")

        stats = self.get_stats()
        print(
            f"\nStats: {stats['total_trades']} trades | "
            f"Win rate: {stats['win_rate']:.1%} | "
            f"PnL: {stats['total_pnl']:+.2f}"
        )
        print("=" * 50 + "\n")
