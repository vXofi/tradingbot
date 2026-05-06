"""
Signal Arbiter — дедупликация и обработка противоречий.

Гейт перед process_event():
- Дублирующий сигнал (тот же тикер + направление в окне) → DROP
- Противоречие с открытой позицией → CONTRADICTION (core решает, как развернуться)
- Иначе → PROCEED
"""

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from .models import NewsEvent, Position, PositionState, Sentiment


class ArbiterDecision(Enum):
    PROCEED = "proceed"
    DUPLICATE = "duplicate"
    CONTRADICTION = "contradiction"


@dataclass
class ArbiterAction:
    """Результат оценки арбитром: решение + контекст."""
    decision: ArbiterDecision
    existing_position: Optional[Position] = None


@dataclass
class RecentSignal:
    ticker: str
    sentiment: Sentiment
    timestamp: datetime


class SignalArbiter:
    """
    Скользящее окно последних сигналов для дедупликации
    и проверка противоречий с открытыми позициями.

    All mutations are serialized by ``_lock`` so concurrent callers
    (e.g. asyncio.gather batches from RSS) cannot interleave
    prune / iterate / append.
    """

    def __init__(self, dedup_window_sec: float = 60.0):
        self._recent: deque[RecentSignal] = deque()
        self._window = timedelta(seconds=dedup_window_sec)
        self._lock = threading.Lock()

    def _prune(self):
        cutoff = datetime.now() - self._window
        while self._recent and self._recent[0].timestamp < cutoff:
            self._recent.popleft()

    def evaluate(
        self,
        event: NewsEvent,
        positions: dict[str, Position],
    ) -> ArbiterAction:
        """
        Оценить входящий сигнал.

        Args:
            event: входящее событие
            positions: словарь figi → Position открытых позиций

        Returns:
            ArbiterAction с решением и, при CONTRADICTION, ссылкой на позицию.
        """
        with self._lock:
            self._prune()

            for sig in self._recent:
                if sig.ticker == event.ticker and sig.sentiment == event.sentiment:
                    return ArbiterAction(decision=ArbiterDecision.DUPLICATE)

            pos = positions.get(event.figi)
            if pos is not None and pos.state == PositionState.ACTIVE:
                opposite = (
                    (pos.direction == Sentiment.BULLISH and event.sentiment == Sentiment.BEARISH)
                    or (pos.direction == Sentiment.BEARISH and event.sentiment == Sentiment.BULLISH)
                )
                if opposite:
                    self._recent.append(
                        RecentSignal(event.ticker, event.sentiment, datetime.now())
                    )
                    return ArbiterAction(
                        decision=ArbiterDecision.CONTRADICTION,
                        existing_position=pos,
                    )

            self._recent.append(
                RecentSignal(event.ticker, event.sentiment, datetime.now())
            )
            return ArbiterAction(decision=ArbiterDecision.PROCEED)
