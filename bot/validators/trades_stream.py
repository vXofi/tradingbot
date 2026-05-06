"""
Лента сделок через T-Invest API.
"""

import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Callable, Optional

from t_tech.invest import Client

from ..models import Trade, Sentiment
from ..utils.retry import async_retry, stream_with_reconnect


class TradesStream:
    """
    Подписка на ленту сделок и анализ объёмов.
    """
    
    def __init__(
        self,
        client: Client,
        history_seconds: int = 60,
        baseline_seconds: int = 300
    ):
        self.client = client
        self.history_seconds = history_seconds
        self.baseline_seconds = baseline_seconds
        
        # История сделок по FIGI
        self._trades: dict[str, deque[Trade]] = {}
        
        # Baseline объёмы (средние за baseline_seconds)
        self._baseline_volume: dict[str, float] = {}
        
        # Callbacks
        self._callbacks: list[Callable] = []
    
    def _convert_trade(self, trade_data, figi: str) -> Trade:
        """Конвертация из API формата."""
        
        def price_to_float(quotation) -> float:
            return quotation.units + quotation.nano / 1e9
        
        dir_name = getattr(trade_data.direction, "name", "")
        if dir_name == "TRADE_DIRECTION_BUY":
            direction = "buy"
        elif dir_name == "TRADE_DIRECTION_SELL":
            direction = "sell"
        else:
            direction = "unknown"
        
        return Trade(
            figi=figi,
            price=price_to_float(trade_data.price),
            quantity=trade_data.quantity,
            direction=direction,
            timestamp=trade_data.time if hasattr(trade_data, 'time') else datetime.now()
        )
    
    def _cleanup_old_trades(self, figi: str):
        """Удаление старых сделок."""
        if figi not in self._trades:
            return
        
        cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=self.baseline_seconds)
        
        while self._trades[figi] and self._trades[figi][0].timestamp < cutoff:
            self._trades[figi].popleft()
    
    def get_recent_trades(self, figi: str, seconds: float = 1.0) -> list[Trade]:
        """Получить сделки за последние N секунд."""
        if figi not in self._trades:
            return []
        
        cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=seconds)
        return [t for t in self._trades[figi] if t.timestamp >= cutoff]
    
    def get_volume(self, figi: str, seconds: float = 1.0) -> int:
        """Суммарный объём за последние N секунд."""
        trades = self.get_recent_trades(figi, seconds)
        return sum(t.quantity for t in trades)
    
    def get_buy_volume(self, figi: str, seconds: float = 1.0) -> int:
        """Объём покупок за последние N секунд."""
        trades = self.get_recent_trades(figi, seconds)
        return sum(t.quantity for t in trades if t.direction == "buy")
    
    def get_sell_volume(self, figi: str, seconds: float = 1.0) -> int:
        """Объём продаж за последние N секунд."""
        trades = self.get_recent_trades(figi, seconds)
        return sum(t.quantity for t in trades if t.direction == "sell")
    
    def get_baseline_volume(self, figi: str) -> float:
        """Средний объём за baseline период (в единицах/секунду)."""
        if figi not in self._trades or len(self._trades[figi]) == 0:
            return 0.0
        
        total_volume = sum(t.quantity for t in self._trades[figi])
        
        if len(self._trades[figi]) < 2:
            return 0.0
        
        time_span = (
            self._trades[figi][-1].timestamp - 
            self._trades[figi][0].timestamp
        ).total_seconds()
        
        if time_span <= 0:
            return 0.0
        
        return total_volume / time_span
    
    def detect_volume_spike(
        self,
        figi: str,
        threshold: float = 3.0,
        window_seconds: float = 1.0
    ) -> tuple[bool, float]:
        """
        Детекция всплеска объёма.
        
        Args:
            figi: Инструмент
            threshold: Во сколько раз объём должен превысить baseline
            window_seconds: Окно измерения
            
        Returns:
            (is_spike, ratio) - есть ли всплеск и во сколько раз
        """
        current_volume = self.get_volume(figi, window_seconds)
        baseline = self.get_baseline_volume(figi) * window_seconds
        
        if baseline <= 0:
            return False, 0.0
        
        ratio = current_volume / baseline
        return ratio >= threshold, ratio
    
    def detect_sentiment_from_trades(
        self,
        figi: str,
        seconds: float = 1.0,
        threshold: float = 0.3
    ) -> Sentiment:
        """
        Определить sentiment по соотношению buy/sell объёмов.
        
        Args:
            figi: Инструмент
            seconds: Окно анализа
            threshold: Порог для определения направления
            
        Returns:
            BULLISH если buy >> sell
            BEARISH если sell >> buy
            NEUTRAL иначе
        """
        buy_vol = self.get_buy_volume(figi, seconds)
        sell_vol = self.get_sell_volume(figi, seconds)
        total = buy_vol + sell_vol
        
        if total == 0:
            return Sentiment.NEUTRAL
        
        # Дисбаланс buy vs sell
        imbalance = (buy_vol - sell_vol) / total
        
        if imbalance > threshold:
            return Sentiment.BULLISH
        elif imbalance < -threshold:
            return Sentiment.BEARISH
        else:
            return Sentiment.NEUTRAL
    
    def get_price_change(self, figi: str, seconds: float = 1.0) -> Optional[float]:
        """
        Изменение цены за последние N секунд (в %).
        """
        trades = self.get_recent_trades(figi, seconds)
        
        if len(trades) < 2:
            return None
        
        first_price = trades[0].price
        last_price = trades[-1].price
        
        if first_price == 0:
            return None
        
        return ((last_price - first_price) / first_price) * 100
    
    async def subscribe(self, figi: str):
        """Подписаться на ленту сделок."""
        if figi not in self._trades:
            self._trades[figi] = deque(maxlen=10000)
    
    def on_trade(self, callback: Callable[[Trade], None]):
        """Добавить callback на новую сделку."""
        self._callbacks.append(callback)
    
    async def stream(self, figi: str) -> AsyncIterator[Trade]:
        """
        Асинхронный генератор сделок.
        Автоматически переподключается при обрыве с exponential backoff.

        Usage:
            async for trade in trades_stream.stream('BBG004730N88'):
                print(f"{trade.direction}: {trade.quantity} @ {trade.price}")
        """
        await self.subscribe(figi)

        def _factory():
            return self.client.market_data_stream.trades(figi=figi)

        async for trade_data in stream_with_reconnect(
            _factory,
            base_delay=2.0,
            max_delay=300.0,
            on_reconnect=lambda attempt, delay: print(
                f"Trades stream reconnect #{attempt} for {figi}, "
                f"retrying in {delay:.1f}s"
            ),
        ):
            trade = self._convert_trade(trade_data, figi)

            self._trades[figi].append(trade)
            self._cleanup_old_trades(figi)

            for callback in self._callbacks:
                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(trade)
                    else:
                        callback(trade)
                except Exception as e:
                    print(f"Trade callback error: {e}")

            yield trade
    
    @async_retry(max_attempts=3, base_delay=1.0, max_delay=10.0)
    async def _fetch_trades_api(self, figi: str, minutes: int) -> list[Trade]:
        """Raw API call for fetch_last_trades(), retries on transient errors."""
        from_time = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        to_time = datetime.now(timezone.utc)

        response = self.client.market_data.get_last_trades(
            figi=figi, from_=from_time, to=to_time
        )
        return [self._convert_trade(td, figi) for td in response.trades]

    async def fetch_last_trades(self, figi: str, minutes: int = 5) -> list[Trade]:
        """
        Загрузить последние сделки (не стрим).
        Полезно для инициализации baseline. Retries up to 3 times.
        """
        try:
            trades = await self._fetch_trades_api(figi, minutes)

            if figi not in self._trades:
                self._trades[figi] = deque(maxlen=10000)
            self._trades[figi].extend(trades)

            return trades
        except Exception as e:
            print(f"Fetch trades error for {figi}: {e}")
            return []

