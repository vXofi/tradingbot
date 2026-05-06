"""
Конфигурация бота.
"""

import json
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv


@dataclass
class TradingConfig:
    """Параметры торговли."""
    max_position_rub: float = 50000.0      # Макс. размер позиции в рублях
    stop_loss_atr_mult: float = 2.0        # Stop-Loss = ATR * множитель
    take_profit_atr_mult: float = 3.0      # Take-Profit = ATR * множитель
    max_position_time_min: int = 15        # Макс. время в позиции (минуты)

    # Trailing stop / reversal
    trailing_stop_pct: float = 1.5             # % просадка от peak → разворот
    momentum_candles: int = 5                  # Сколько 1-мин свечей проверять
    momentum_bearish_ratio: float = 0.8        # 80 % медвежьих = разворот
    momentum_check_interval_sec: float = 30.0  # Тротл для вызовов API свечей

    # Position monitoring
    monitor_interval_sec: float = 2.0          # Price check interval (seconds)

    # Signal arbiter
    dedup_window_sec: float = 60.0             # Окно дедупликации сигналов

    def __post_init__(self):
        if self.max_position_rub <= 0:
            raise ValueError(f"max_position_rub must be > 0, got {self.max_position_rub}")
        if self.stop_loss_atr_mult <= 0:
            raise ValueError(f"stop_loss_atr_mult must be > 0, got {self.stop_loss_atr_mult}")
        if self.take_profit_atr_mult <= 0:
            raise ValueError(f"take_profit_atr_mult must be > 0, got {self.take_profit_atr_mult}")
        if self.max_position_time_min <= 0:
            raise ValueError(f"max_position_time_min must be > 0, got {self.max_position_time_min}")
        if not (0 < self.trailing_stop_pct <= 50):
            raise ValueError(f"trailing_stop_pct must be in (0, 50], got {self.trailing_stop_pct}")
        if not (1 <= self.momentum_candles <= 60):
            raise ValueError(f"momentum_candles must be in [1, 60], got {self.momentum_candles}")
        if not (0.0 <= self.momentum_bearish_ratio <= 1.0):
            raise ValueError(f"momentum_bearish_ratio must be in [0.0, 1.0], got {self.momentum_bearish_ratio}")
        if self.momentum_check_interval_sec < 1.0:
            raise ValueError(f"momentum_check_interval_sec must be >= 1.0, got {self.momentum_check_interval_sec}")
        if self.monitor_interval_sec < 0.5:
            raise ValueError(f"monitor_interval_sec must be >= 0.5, got {self.monitor_interval_sec}")
        if self.dedup_window_sec <= 0:
            raise ValueError(f"dedup_window_sec must be > 0, got {self.dedup_window_sec}")
        # Warnings for suspicious but technically valid values
        if self.trailing_stop_pct > 10:
            warnings.warn(f"trailing_stop_pct={self.trailing_stop_pct}% is unusually high", stacklevel=2)
        if self.take_profit_atr_mult < self.stop_loss_atr_mult:
            warnings.warn("take_profit_atr_mult < stop_loss_atr_mult — reward/risk ratio < 1", stacklevel=2)


@dataclass
class ValidationConfig:
    """Параметры валидации Order Flow."""
    volume_spike_threshold: float = 3.0    # Объём > avg * threshold = spike
    imbalance_threshold: float = 0.3       # |imbalance| > threshold = сигнал
    price_move_threshold: float = 0.1      # Движение цены > % = подтверждение
    reaction_window_sec: float = 8.0       # Окно для подтверждения (секунды)
    min_confidence: float = 0.4            # Мин. уверенность для сигнала

    def __post_init__(self):
        if self.volume_spike_threshold <= 1.0:
            raise ValueError(f"volume_spike_threshold must be > 1.0, got {self.volume_spike_threshold}")
        if not (0.0 <= self.imbalance_threshold <= 1.0):
            raise ValueError(f"imbalance_threshold must be in [0.0, 1.0], got {self.imbalance_threshold}")
        if self.price_move_threshold < 0:
            raise ValueError(f"price_move_threshold must be >= 0, got {self.price_move_threshold}")
        if self.reaction_window_sec <= 0:
            raise ValueError(f"reaction_window_sec must be > 0, got {self.reaction_window_sec}")
        if not (0.0 <= self.min_confidence <= 1.0):
            raise ValueError(f"min_confidence must be in [0.0, 1.0], got {self.min_confidence}")


@dataclass
class Config:
    """Главная конфигурация."""
    # API
    tinkoff_token: str
    tinkoff_app_name: str = "event-driven-bot"
    use_sandbox: bool = True
    
    # Trading
    trading: TradingConfig = field(default_factory=TradingConfig)
    
    # Validation
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    
    # Whitelist
    whitelist: dict[str, str] = field(default_factory=dict)  # ticker -> figi
    
    # Paths
    data_dir: Path = field(default_factory=lambda: Path("data"))
    
    @classmethod
    def from_env(cls, env_path: Optional[str] = None) -> "Config":
        """Загрузка конфигурации из .env и data файлов."""
        load_dotenv(env_path)
        
        token = os.getenv("TOKEN_TINKOFF")
        if not token:
            raise ValueError("TOKEN_TINKOFF not found in environment")
        
        config = cls(
            tinkoff_token=token,
            use_sandbox=os.getenv("USE_SANDBOX", "true").lower() == "true",
        )
        
        # Загружаем whitelist
        config._load_whitelist()
        
        return config
    
    def _load_whitelist(self):
        """Загрузка whitelist из JSON."""
        whitelist_path = self.data_dir / "whitelist.json"
        
        if whitelist_path.exists():
            with open(whitelist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data.get("instruments", []):
                    self.whitelist[item["ticker"]] = item["figi"]
    
    def get_figi(self, ticker: str) -> Optional[str]:
        """Получить FIGI по тикеру."""
        return self.whitelist.get(ticker.upper())
    
    def is_allowed(self, ticker: str) -> bool:
        """Проверить, есть ли тикер в whitelist."""
        return ticker.upper() in self.whitelist

