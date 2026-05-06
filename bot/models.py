"""
Модели данных для всего бота.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Sentiment(Enum):
    """Направление сигнала."""
    BULLISH = "bullish"    # Ожидаем рост
    BEARISH = "bearish"    # Ожидаем падение
    NEUTRAL = "neutral"    # Неопределённость / нет сигнала


class ValidationResult(Enum):
    """Результат валидации Order Flow."""
    CONFIRMED = "confirmed"      # Сигнал подтверждён
    REJECTED = "rejected"        # Сигнал не подтверждён
    INCONCLUSIVE = "inconclusive"  # Недостаточно данных


class PositionState(Enum):
    """Состояние позиции (state machine guard)."""
    ACTIVE = "active"        # Нормальный мониторинг
    REVERSING = "reversing"  # Закрытие + попытка разворота (блокирует повторный вход)
    CLOSED = "closed"        # Позиция закрыта


@dataclass
class NewsEvent:
    """Событие из источника новостей."""
    ticker: str                    # 'SBER'
    figi: str                      # 'BBG004730N88'
    sentiment: Sentiment           # BULLISH / BEARISH / NEUTRAL
    confidence: float              # 0.0 - 1.0, уверенность в сигнале
    keywords_found: list[str]      # ['дивиденды', 'рекомендовать']
    timestamp: datetime
    source: str                    # Источник: 'telegram', 'manual', 'rss'
    raw_text: Optional[str] = None


@dataclass
class OrderBookSnapshot:
    """Снимок стакана."""
    figi: str
    timestamp: datetime
    bids: list[tuple[float, int]]  # [(цена, объём), ...]
    asks: list[tuple[float, int]]  # [(цена, объём), ...]
    
    @property
    def bid_volume(self) -> int:
        """Суммарный объём bid."""
        return sum(qty for _, qty in self.bids)
    
    @property
    def ask_volume(self) -> int:
        """Суммарный объём ask."""
        return sum(qty for _, qty in self.asks)
    
    @property
    def imbalance(self) -> float:
        """
        Дисбаланс стакана.
        > 0: перевес покупателей
        < 0: перевес продавцов
        0: баланс
        """
        total = self.bid_volume + self.ask_volume
        if total == 0:
            return 0.0
        return (self.bid_volume - self.ask_volume) / total
    
    @property
    def best_bid(self) -> Optional[float]:
        """Лучшая цена покупки."""
        return self.bids[0][0] if self.bids else None
    
    @property
    def best_ask(self) -> Optional[float]:
        """Лучшая цена продажи."""
        return self.asks[0][0] if self.asks else None
    
    @property
    def spread(self) -> Optional[float]:
        """Спред в абсолютных единицах."""
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None


@dataclass
class Trade:
    """Сделка из ленты."""
    figi: str
    price: float
    quantity: int
    direction: str        # 'buy' / 'sell'
    timestamp: datetime


@dataclass
class FlowAnalysis:
    """Результат анализа Order Flow."""
    figi: str
    timestamp: datetime
    
    # Метрики
    imbalance: float              # Дисбаланс стакана (-1 to 1)
    volume_ratio: float           # Текущий объём / средний объём
    price_change_percent: float   # Изменение цены в %
    
    # Результат
    detected_sentiment: Sentiment  # Что показывает Order Flow
    validation_result: ValidationResult
    confidence: float             # 0.0 - 1.0
    
    # Детали
    details: dict = field(default_factory=dict)


@dataclass
class Position:
    """Открытая позиция."""
    figi: str
    ticker: str
    direction: Sentiment          # BULLISH = long, BEARISH = short
    entry_price: float
    quantity: int
    entry_time: datetime
    
    # Risk management
    stop_loss: float
    take_profit: Optional[float]
    atr: float                    # ATR на момент входа

    # Trailing / reversal tracking
    peak_price: float = 0.0               # Лучшая цена с момента входа
    state: PositionState = field(default=PositionState.ACTIVE)

    # Статус
    is_open: bool = True
    exit_price: Optional[float] = None
    exit_time: Optional[datetime] = None
    exit_reason: Optional[str] = None
    pnl: Optional[float] = None

    def __post_init__(self):
        if self.peak_price == 0.0:
            self.peak_price = self.entry_price


@dataclass
class ExitSignal:
    """Унифицированный сигнал выхода из позиции."""
    figi: str
    ticker: str
    reason: str               # STOP_LOSS, TAKE_PROFIT, TIME_LIMIT, TRAILING_STOP, MOMENTUM
    current_price: float
    should_reverse: bool      # True для TRAILING_STOP / MOMENTUM
    position: 'Position'


@dataclass
class ReversalContext:
    """Контекст для попытки разворота позиции."""
    original_position: 'Position'
    reason: str                    # TRAILING_STOP, MOMENTUM, CONTRADICTION
    reversed_sentiment: Sentiment  # Направление разворота
    current_price: float
    figi: str
    ticker: str

