"""
Стрим стакана через T-Invest API.
"""

import asyncio
from collections import deque
from datetime import datetime
from typing import AsyncIterator, Callable, Optional

from t_tech.invest import Client
from t_tech.invest.schemas import OrderBook

from ..models import OrderBookSnapshot, Sentiment
from ..utils.retry import async_retry, stream_with_reconnect


class OrderBookStream:
    """
    Подписка на стакан и расчёт метрик.
    """
    
    def __init__(
        self,
        client: Client,
        depth: int = 20,
        history_size: int = 100
    ):
        self.client = client
        self.depth = depth
        self.history_size = history_size
        
        # История снимков стакана по FIGI
        self._history: dict[str, deque[OrderBookSnapshot]] = {}
        
        # Текущие подписки
        self._subscriptions: set[str] = set()
        
        # Callbacks
        self._callbacks: list[Callable] = []
    
    def _convert_orderbook(self, ob: OrderBook) -> OrderBookSnapshot:
        """Конвертация из API формата в наш."""
        
        def price_to_float(quotation) -> float:
            return quotation.units + quotation.nano / 1e9
        
        bids = [
            (price_to_float(level.price), level.quantity)
            for level in ob.bids
        ]
        asks = [
            (price_to_float(level.price), level.quantity)
            for level in ob.asks
        ]
        
        return OrderBookSnapshot(
            figi=ob.figi,
            timestamp=datetime.now(),
            bids=bids,
            asks=asks
        )
    
    def get_latest(self, figi: str) -> Optional[OrderBookSnapshot]:
        """Получить последний снимок стакана."""
        history = self._history.get(figi)
        if history and len(history) > 0:
            return history[-1]
        return None
    
    def get_history(self, figi: str, n: int = 10) -> list[OrderBookSnapshot]:
        """Получить последние N снимков."""
        history = self._history.get(figi, deque())
        return list(history)[-n:]
    
    def calculate_imbalance(self, figi: str) -> Optional[float]:
        """
        Рассчитать текущий дисбаланс стакана.
        
        Returns:
            float: от -1 (все продают) до +1 (все покупают)
            None: если нет данных
        """
        snapshot = self.get_latest(figi)
        if snapshot:
            return snapshot.imbalance
        return None
    
    def calculate_imbalance_change(self, figi: str, periods: int = 5) -> Optional[float]:
        """
        Изменение дисбаланса за последние N снимков.
        Положительное = покупатели усиливаются.
        """
        history = self.get_history(figi, periods)
        if len(history) < 2:
            return None
        
        return history[-1].imbalance - history[0].imbalance
    
    def detect_sentiment_from_imbalance(
        self,
        figi: str,
        threshold: float = 0.3
    ) -> Sentiment:
        """
        Определить sentiment по дисбалансу стакана.
        
        Args:
            figi: Инструмент
            threshold: Порог для определения направления
            
        Returns:
            BULLISH если imbalance > threshold
            BEARISH если imbalance < -threshold
            NEUTRAL иначе
        """
        imbalance = self.calculate_imbalance(figi)
        
        if imbalance is None:
            return Sentiment.NEUTRAL
        
        if imbalance > threshold:
            return Sentiment.BULLISH
        elif imbalance < -threshold:
            return Sentiment.BEARISH
        else:
            return Sentiment.NEUTRAL
    
    async def subscribe(self, figi: str):
        """Подписаться на стакан инструмента."""
        if figi not in self._history:
            self._history[figi] = deque(maxlen=self.history_size)
        self._subscriptions.add(figi)
    
    async def unsubscribe(self, figi: str):
        """Отписаться от стакана."""
        self._subscriptions.discard(figi)
    
    def on_update(self, callback: Callable[[OrderBookSnapshot], None]):
        """Добавить callback на обновление стакана."""
        self._callbacks.append(callback)
    
    async def _process_orderbook(self, orderbook: OrderBook):
        """Обработка нового снимка стакана."""
        snapshot = self._convert_orderbook(orderbook)
        
        # Сохраняем в историю
        if snapshot.figi not in self._history:
            self._history[snapshot.figi] = deque(maxlen=self.history_size)
        self._history[snapshot.figi].append(snapshot)
        
        # Вызываем callbacks
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(snapshot)
                else:
                    callback(snapshot)
            except Exception as e:
                print(f"OrderBook callback error: {e}")
    
    async def stream(self, figi: str) -> AsyncIterator[OrderBookSnapshot]:
        """
        Асинхронный генератор снимков стакана.
        Автоматически переподключается при обрыве с exponential backoff.

        Usage:
            async for snapshot in orderbook_stream.stream('BBG004730N88'):
                print(snapshot.imbalance)
        """
        await self.subscribe(figi)

        def _factory():
            return self.client.market_data_stream.order_book(
                figi=figi, depth=self.depth
            )

        async for orderbook in stream_with_reconnect(
            _factory,
            base_delay=2.0,
            max_delay=300.0,
            on_reconnect=lambda attempt, delay: print(
                f"OrderBook stream reconnect #{attempt} for {figi}, "
                f"retrying in {delay:.1f}s"
            ),
        ):
            snapshot = self._convert_orderbook(orderbook)
            self._history[figi].append(snapshot)
            yield snapshot
    
    @async_retry(max_attempts=3, base_delay=1.0, max_delay=10.0)
    async def _poll_api(self, figi: str) -> OrderBookSnapshot:
        """Raw API call for poll(), retries on transient errors."""
        response = self.client.market_data.get_order_book(
            figi=figi, depth=self.depth
        )
        return self._convert_orderbook(response)

    async def poll(self, figi: str) -> Optional[OrderBookSnapshot]:
        """
        Одноразовый запрос стакана (не стрим).
        Полезно для тестирования. Retries up to 3 times.
        """
        try:
            snapshot = await self._poll_api(figi)

            if figi not in self._history:
                self._history[figi] = deque(maxlen=self.history_size)
            self._history[figi].append(snapshot)

            return snapshot
        except Exception as e:
            print(f"OrderBook poll error for {figi}: {e}")
            return None

