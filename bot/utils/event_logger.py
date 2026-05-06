"""
Event Logger — логирование всех событий бота.

Сохраняет события в JSON для анализа и отладки.
"""

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional


class EventType(Enum):
    """Типы событий."""
    # Parsing
    NEWS_PARSED = "news_parsed"
    NEWS_IGNORED = "news_ignored"
    
    # Validation
    SIGNAL_VALIDATED = "signal_validated"
    SIGNAL_REJECTED = "signal_rejected"
    SIGNAL_INCONCLUSIVE = "signal_inconclusive"
    
    # Trading
    ORDER_PLACED = "order_placed"
    ORDER_FAILED = "order_failed"
    ORDER_CANCELLED = "order_cancelled"
    
    # Positions
    POSITION_OPENED = "position_opened"
    POSITION_CLOSED = "position_closed"
    STOP_LOSS_HIT = "stop_loss_hit"
    TAKE_PROFIT_HIT = "take_profit_hit"
    TIME_LIMIT_HIT = "time_limit_hit"

    # Arbiter / reversal
    SIGNAL_DUPLICATE = "signal_duplicate"
    SIGNAL_CONTRADICTION = "signal_contradiction"
    TRAILING_STOP_HIT = "trailing_stop_hit"
    MOMENTUM_EXIT = "momentum_exit"
    POSITION_REVERSED = "position_reversed"

    # Resilience
    STREAM_RECONNECT = "stream_reconnect"
    API_RETRY = "api_retry"
    RATE_LIMITED = "rate_limited"

    # System
    BOT_STARTED = "bot_started"
    BOT_STOPPED = "bot_stopped"
    ERROR = "error"


@dataclass
class LogEvent:
    """Структура события для лога."""
    timestamp: str
    event_type: str
    ticker: Optional[str] = None
    figi: Optional[str] = None
    sentiment: Optional[str] = None
    confidence: Optional[float] = None
    price: Optional[float] = None
    quantity: Optional[int] = None
    pnl: Optional[float] = None
    reason: Optional[str] = None
    details: dict = field(default_factory=dict)
    
    def to_dict(self) -> dict:
        """Конвертация в словарь (без None значений)."""
        result = {}
        for key, value in asdict(self).items():
            if value is not None and value != {}:
                result[key] = value
        return result


class EventLogger:
    """
    Логгер событий.
    
    Сохраняет события в:
    - logs/events_YYYY-MM-DD.json (структурированные данные)
    - logs/events_YYYY-MM-DD.log (человекочитаемый формат)
    """
    
    def __init__(
        self,
        log_dir: Path = Path("logs"),
        console_output: bool = True,
        file_output: bool = True
    ):
        self.log_dir = Path(log_dir)
        self.console_output = console_output
        self.file_output = file_output
        
        self.max_log_age_days = 30

        # Создаём директорию
        if self.file_output:
            self.log_dir.mkdir(exist_ok=True)
            self._rotate_old_logs()

        # Счётчики для статистики
        self._event_counts: dict[str, int] = {}
        
        # Subscribers receive every LogEvent dict in real-time
        self._subscribers: list[Callable[[dict], None]] = []
    
    def _rotate_old_logs(self):
        """Delete log files older than max_log_age_days."""
        import time as _time
        cutoff = _time.time() - self.max_log_age_days * 86400
        removed = 0
        for f in self.log_dir.iterdir():
            if f.suffix in (".jsonl", ".log") and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        if removed:
            print(f"Log rotation: removed {removed} file(s) older than {self.max_log_age_days} days")

    def _get_log_files(self) -> tuple[Path, Path]:
        """Получить пути к файлам логов на сегодня."""
        today = datetime.now().strftime("%Y-%m-%d")
        jsonl_file = self.log_dir / f"events_{today}.jsonl"
        text_file = self.log_dir / f"events_{today}.log"
        return jsonl_file, text_file
    
    def _format_console(self, event: LogEvent) -> str:
        """Форматирование для консоли."""
        emoji = {
            EventType.NEWS_PARSED.value: "📰",
            EventType.NEWS_IGNORED.value: "🚫",
            EventType.SIGNAL_VALIDATED.value: "✅",
            EventType.SIGNAL_REJECTED.value: "❌",
            EventType.SIGNAL_INCONCLUSIVE.value: "⚪",
            EventType.ORDER_PLACED.value: "📋",
            EventType.ORDER_FAILED.value: "💥",
            EventType.POSITION_OPENED.value: "📈",
            EventType.POSITION_CLOSED.value: "📉",
            EventType.STOP_LOSS_HIT.value: "🛑",
            EventType.TAKE_PROFIT_HIT.value: "🎯",
            EventType.TIME_LIMIT_HIT.value: "⏰",
            EventType.SIGNAL_DUPLICATE.value: "🔁",
            EventType.SIGNAL_CONTRADICTION.value: "⚡",
            EventType.TRAILING_STOP_HIT.value: "📉",
            EventType.MOMENTUM_EXIT.value: "📊",
            EventType.POSITION_REVERSED.value: "🔄",
            EventType.BOT_STARTED.value: "🚀",
            EventType.BOT_STOPPED.value: "🛑",
            EventType.ERROR.value: "💥",
        }.get(event.event_type, "📝")
        
        time_str = event.timestamp.split("T")[1].split(".")[0]  # HH:MM:SS
        
        parts = [f"[{time_str}] {emoji} {event.event_type}"]
        
        if event.ticker:
            parts.append(f"[{event.ticker}]")
        
        if event.sentiment:
            sent_emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}.get(event.sentiment, "")
            parts.append(f"{sent_emoji}{event.sentiment}")
        
        if event.confidence is not None:
            parts.append(f"{event.confidence:.0%}")
        
        if event.price is not None:
            parts.append(f"@{event.price:.2f}")
        
        if event.pnl is not None:
            pnl_str = f"+{event.pnl:.2f}" if event.pnl >= 0 else f"{event.pnl:.2f}"
            parts.append(f"PnL:{pnl_str}")
        
        if event.reason:
            parts.append(f"({event.reason})")
        
        return " ".join(parts)
    
    def _format_text(self, event: LogEvent) -> str:
        """Форматирование для текстового файла."""
        parts = [event.timestamp, event.event_type]
        
        if event.ticker:
            parts.append(event.ticker)
        if event.sentiment:
            parts.append(event.sentiment)
        if event.confidence is not None:
            parts.append(f"conf={event.confidence:.2f}")
        if event.price is not None:
            parts.append(f"price={event.price:.2f}")
        if event.pnl is not None:
            parts.append(f"pnl={event.pnl:.2f}")
        if event.reason:
            parts.append(f"reason={event.reason}")
        if event.details:
            parts.append(f"details={event.details}")
        
        return " | ".join(parts)
    
    def log(
        self,
        event_type: EventType,
        ticker: Optional[str] = None,
        figi: Optional[str] = None,
        sentiment: Optional[str] = None,
        confidence: Optional[float] = None,
        price: Optional[float] = None,
        quantity: Optional[int] = None,
        pnl: Optional[float] = None,
        reason: Optional[str] = None,
        details: Optional[dict] = None
    ):
        """
        Залогировать событие.
        
        Args:
            event_type: Тип события
            ticker: Тикер инструмента
            figi: FIGI
            sentiment: bullish/bearish/neutral
            confidence: Уверенность 0-1
            price: Цена
            quantity: Количество
            pnl: Прибыль/убыток
            reason: Причина/описание
            details: Дополнительные данные
        """
        event = LogEvent(
            timestamp=datetime.now().isoformat(),
            event_type=event_type.value,
            ticker=ticker,
            figi=figi,
            sentiment=sentiment,
            confidence=confidence,
            price=price,
            quantity=quantity,
            pnl=pnl,
            reason=reason,
            details=details or {}
        )
        
        # Счётчик
        self._event_counts[event_type.value] = self._event_counts.get(event_type.value, 0) + 1
        
        # Notify subscribers
        event_dict = event.to_dict()
        for sub in self._subscribers:
            try:
                sub(event_dict)
            except Exception:
                pass
        
        # Консоль
        if self.console_output:
            print(self._format_console(event))
        
        if self.file_output:
            jsonl_file, text_file = self._get_log_files()
            
            try:
                with open(jsonl_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(event_dict, ensure_ascii=False) + "\n")
            except Exception as e:
                print(f"⚠️ JSONL log error: {e}")
            
            try:
                with open(text_file, "a", encoding="utf-8") as f:
                    f.write(self._format_text(event) + "\n")
            except Exception as e:
                print(f"⚠️ Text log error: {e}")
    
    # Convenience methods
    def news_parsed(
        self,
        ticker: str,
        sentiment: str,
        confidence: float,
        keywords: list[str],
        method: str
    ):
        """Лог: новость распарсена."""
        self.log(
            EventType.NEWS_PARSED,
            ticker=ticker,
            sentiment=sentiment,
            confidence=confidence,
            details={"keywords": keywords, "method": method}
        )
    
    def news_ignored(self, reason: str, text_preview: str = ""):
        """Лог: новость проигнорирована."""
        self.log(
            EventType.NEWS_IGNORED,
            reason=reason,
            details={"text": text_preview[:100]}
        )
    
    def signal_validated(
        self,
        ticker: str,
        sentiment: str,
        confidence: float,
        imbalance: float,
        volume_ratio: float
    ):
        """Лог: сигнал подтверждён Order Flow."""
        self.log(
            EventType.SIGNAL_VALIDATED,
            ticker=ticker,
            sentiment=sentiment,
            confidence=confidence,
            details={"imbalance": imbalance, "volume_ratio": volume_ratio}
        )
    
    def signal_rejected(self, ticker: str, reason: str):
        """Лог: сигнал отклонён."""
        self.log(EventType.SIGNAL_REJECTED, ticker=ticker, reason=reason)
    
    def position_opened(
        self,
        ticker: str,
        sentiment: str,
        price: float,
        quantity: int,
        stop_loss: float,
        take_profit: float
    ):
        """Лог: позиция открыта."""
        self.log(
            EventType.POSITION_OPENED,
            ticker=ticker,
            sentiment=sentiment,
            price=price,
            quantity=quantity,
            details={"stop_loss": stop_loss, "take_profit": take_profit}
        )
    
    def position_closed(
        self,
        ticker: str,
        price: float,
        pnl: float,
        reason: str
    ):
        """Лог: позиция закрыта."""
        event_type = {
            "STOP_LOSS": EventType.STOP_LOSS_HIT,
            "TAKE_PROFIT": EventType.TAKE_PROFIT_HIT,
            "TIME_LIMIT": EventType.TIME_LIMIT_HIT,
            "TRAILING_STOP": EventType.TRAILING_STOP_HIT,
            "MOMENTUM": EventType.MOMENTUM_EXIT,
            "CONTRADICTION": EventType.SIGNAL_CONTRADICTION,
        }.get(reason, EventType.POSITION_CLOSED)

        self.log(event_type, ticker=ticker, price=price, pnl=pnl, reason=reason)
    
    def error(self, message: str, details: Optional[dict] = None):
        """Лог: ошибка."""
        self.log(EventType.ERROR, reason=message, details=details)
    
    def subscribe(self, callback: Callable[[dict], None]):
        """Register a subscriber to receive every log event dict."""
        self._subscribers.append(callback)
    
    def unsubscribe(self, callback: Callable[[dict], None]):
        """Remove a subscriber."""
        self._subscribers = [s for s in self._subscribers if s is not callback]
    
    def get_stats(self) -> dict:
        """Получить статистику событий."""
        return {
            "event_counts": self._event_counts.copy(),
            "total_events": sum(self._event_counts.values())
        }
    
    def get_today_events(self) -> list[dict]:
        """Получить все события за сегодня (reads JSONL)."""
        jsonl_file, _ = self._get_log_files()
        
        if not jsonl_file.exists():
            return []
        
        events = []
        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            events.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception:
            pass
        return events

