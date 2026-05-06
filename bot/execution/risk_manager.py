"""
Risk Manager — управление рисками.

Функции:
- Расчёт размера позиции
- Расчёт Stop-Loss на основе ATR
- Расчёт Take-Profit
- Проверка лимитов
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from t_tech.invest import Client
from t_tech.invest.schemas import CandleInterval

from ..config import Config
from ..models import Position, Sentiment


@dataclass
class RiskLimits:
    """Лимиты риска."""
    max_position_rub: float
    max_positions_count: int = 3
    max_daily_loss_rub: float = 10000.0
    max_single_loss_percent: float = 2.0  # % от позиции


class RiskManager:
    """
    Управление рисками.
    """
    
    def __init__(self, client: Client, config: Config):
        self.client = client
        self.config = config
        
        # Дневная статистика
        self._daily_pnl: float = 0.0
        self._daily_trades: int = 0
        self._last_reset: datetime = datetime.now()
        
        # Лимиты
        self.limits = RiskLimits(
            max_position_rub=config.trading.max_position_rub
        )
    
    def _reset_daily_stats_if_needed(self):
        """Сброс дневной статистики в полночь."""
        now = datetime.now()
        if now.date() > self._last_reset.date():
            self._daily_pnl = 0.0
            self._daily_trades = 0
            self._last_reset = now
    
    async def calculate_atr(
        self,
        figi: str,
        period: int = 14,
        interval: CandleInterval = CandleInterval.CANDLE_INTERVAL_5_MIN
    ) -> float:
        """
        Расчёт Average True Range (ATR).
        
        ATR измеряет волатильность инструмента.
        Используется для динамического Stop-Loss.
        
        Args:
            figi: FIGI инструмента
            period: Период ATR (обычно 14)
            interval: Интервал свечей
            
        Returns:
            ATR в абсолютных единицах цены
        """
        # Запрашиваем свечи
        from_time = datetime.now(tz=timezone.utc) - timedelta(hours=2)
        to_time = datetime.now(tz=timezone.utc)
        
        try:
            response = self.client.market_data.get_candles(
                figi=figi,
                from_=from_time,
                to=to_time,
                interval=interval
            )
            
            candles = response.candles
            
            if len(candles) < period + 1:
                # Недостаточно данных — используем fallback
                if candles:
                    # Примерный ATR = (high - low) последней свечи
                    last = candles[-1]
                    return self._quotation_to_float(last.high) - self._quotation_to_float(last.low)
                return 0.0
            
            # Рассчитываем True Range для каждой свечи
            true_ranges = []
            
            for i in range(1, len(candles)):
                high = self._quotation_to_float(candles[i].high)
                low = self._quotation_to_float(candles[i].low)
                prev_close = self._quotation_to_float(candles[i - 1].close)
                
                # True Range = max(high-low, |high-prev_close|, |low-prev_close|)
                tr = max(
                    high - low,
                    abs(high - prev_close),
                    abs(low - prev_close)
                )
                true_ranges.append(tr)
            
            # ATR = среднее True Range за период
            atr = sum(true_ranges[-period:]) / min(period, len(true_ranges))
            
            return atr
            
        except Exception as e:
            print(f"ATR calculation error: {e}")
            return 0.0
    
    def _quotation_to_float(self, quotation) -> float:
        """Конвертация Quotation в float."""
        return quotation.units + quotation.nano / 1e9
    
    def calculate_position_size(
        self,
        price: float,
        lot_size: int = 1,
        max_rub: Optional[float] = None
    ) -> int:
        """
        Расчёт размера позиции в лотах.
        
        Args:
            price: Цена одной акции
            lot_size: Размер лота (акций в 1 лоте)
            max_rub: Максимальная сумма (None = из конфига)
            
        Returns:
            Количество лотов
        """
        max_rub = max_rub or self.limits.max_position_rub
        
        # Стоимость 1 лота
        lot_cost = price * lot_size
        
        if lot_cost <= 0:
            return 0
        
        # Количество лотов
        lots = int(max_rub / lot_cost)
        
        return max(lots, 0)
    
    def calculate_stop_loss(
        self,
        entry_price: float,
        direction: Sentiment,
        atr: float,
        multiplier: Optional[float] = None
    ) -> float:
        """
        Расчёт цены Stop-Loss.
        
        Stop-Loss = Entry ± (ATR * multiplier)
        
        Args:
            entry_price: Цена входа
            direction: BULLISH (long) или BEARISH (short)
            atr: ATR инструмента
            multiplier: Множитель ATR (None = из конфига)
            
        Returns:
            Цена Stop-Loss
        """
        multiplier = multiplier or self.config.trading.stop_loss_atr_mult
        offset = atr * multiplier
        
        if direction == Sentiment.BULLISH:
            # Long позиция: стоп ниже входа
            return entry_price - offset
        elif direction == Sentiment.BEARISH:
            # Short позиция: стоп выше входа
            return entry_price + offset
        else:
            # Neutral: не должно быть позиции
            return entry_price
    
    def calculate_take_profit(
        self,
        entry_price: float,
        direction: Sentiment,
        atr: float,
        multiplier: Optional[float] = None
    ) -> float:
        """
        Расчёт цены Take-Profit.
        
        Take-Profit = Entry ± (ATR * multiplier)
        
        Args:
            entry_price: Цена входа
            direction: BULLISH или BEARISH
            atr: ATR инструмента
            multiplier: Множитель ATR (None = из конфига)
            
        Returns:
            Цена Take-Profit
        """
        multiplier = multiplier or self.config.trading.take_profit_atr_mult
        offset = atr * multiplier
        
        if direction == Sentiment.BULLISH:
            # Long: тейк выше входа
            return entry_price + offset
        elif direction == Sentiment.BEARISH:
            # Short: тейк ниже входа
            return entry_price - offset
        else:
            return entry_price
    
    def check_stop_loss(self, position: Position, current_price: float) -> bool:
        """
        Проверка срабатывания Stop-Loss.
        
        Returns:
            True если стоп сработал
        """
        if position.direction == Sentiment.BULLISH:
            return current_price <= position.stop_loss
        elif position.direction == Sentiment.BEARISH:
            return current_price >= position.stop_loss
        return False
    
    def check_take_profit(self, position: Position, current_price: float) -> bool:
        """
        Проверка срабатывания Take-Profit.
        
        Returns:
            True если тейк сработал
        """
        if position.take_profit is None:
            return False
        
        if position.direction == Sentiment.BULLISH:
            return current_price >= position.take_profit
        elif position.direction == Sentiment.BEARISH:
            return current_price <= position.take_profit
        return False
    
    def check_time_limit(self, position: Position) -> bool:
        """
        Проверка превышения времени в позиции.
        
        Returns:
            True если время вышло
        """
        max_minutes = self.config.trading.max_position_time_min
        elapsed = (datetime.now() - position.entry_time).total_seconds() / 60
        return elapsed >= max_minutes
    
    def can_open_position(
        self,
        price: float,
        quantity: int,
        current_positions: int = 0
    ) -> tuple[bool, str]:
        """
        Проверка возможности открыть позицию.
        
        Returns:
            (можно ли, причина отказа)
        """
        self._reset_daily_stats_if_needed()
        
        # Проверка количества позиций
        if current_positions >= self.limits.max_positions_count:
            return False, f"Max positions limit ({self.limits.max_positions_count})"
        
        # Проверка дневного убытка
        if self._daily_pnl <= -self.limits.max_daily_loss_rub:
            return False, f"Daily loss limit ({self.limits.max_daily_loss_rub} RUB)"
        
        # Проверка размера позиции
        position_cost = price * quantity
        if position_cost > self.limits.max_position_rub:
            return False, f"Position size limit ({self.limits.max_position_rub} RUB)"
        
        return True, "OK"
    
    def calculate_pnl(self, position: Position, exit_price: float) -> float:
        """
        Расчёт P&L позиции.
        
        Returns:
            Прибыль/убыток в рублях
        """
        if position.direction == Sentiment.BULLISH:
            # Long: прибыль = (exit - entry) * qty
            pnl = (exit_price - position.entry_price) * position.quantity
        elif position.direction == Sentiment.BEARISH:
            # Short: прибыль = (entry - exit) * qty
            pnl = (position.entry_price - exit_price) * position.quantity
        else:
            pnl = 0.0
        
        return pnl
    
    def record_trade(self, pnl: float):
        """Записать результат сделки."""
        self._reset_daily_stats_if_needed()
        self._daily_pnl += pnl
        self._daily_trades += 1
    
    # ── trailing stop / momentum (pure calculations) ────────

    def check_trailing_stop(
        self, position: Position, current_price: float
    ) -> bool:
        """
        Проверка срабатывания trailing stop.

        Trailing stop рассчитывается как процент просадки от peak_price.
        Не мутирует позицию — только читает peak_price.

        Returns:
            True если trailing stop сработал.
        """
        if position.peak_price <= 0:
            return False

        pct = self.config.trading.trailing_stop_pct / 100.0

        if position.direction == Sentiment.BULLISH:
            return current_price <= position.peak_price * (1 - pct)
        elif position.direction == Sentiment.BEARISH:
            return current_price >= position.peak_price * (1 + pct)
        return False

    async def check_momentum(self, figi: str) -> Sentiment:
        """
        Определить текущий impuls свечей (бычий / медвежий / нейтральный).

        Запрашивает последние N 1-минутных свечей и считает долю
        медвежьих (close < open) и бычьих.

        Returns:
            Sentiment преобладающего направления.
        """
        n = self.config.trading.momentum_candles
        threshold = self.config.trading.momentum_bearish_ratio

        from_time = datetime.now(tz=timezone.utc) - timedelta(minutes=n + 2)
        to_time = datetime.now(tz=timezone.utc)

        try:
            response = self.client.market_data.get_candles(
                figi=figi,
                from_=from_time,
                to=to_time,
                interval=CandleInterval.CANDLE_INTERVAL_1_MIN,
            )
            candles = response.candles[-n:] if len(response.candles) >= n else response.candles

            if len(candles) < 3:
                return Sentiment.NEUTRAL

            bearish = sum(
                1
                for c in candles
                if self._quotation_to_float(c.close) < self._quotation_to_float(c.open)
            )
            bullish = len(candles) - bearish
            ratio_bear = bearish / len(candles)
            ratio_bull = bullish / len(candles)

            if ratio_bear >= threshold:
                return Sentiment.BEARISH
            if ratio_bull >= threshold:
                return Sentiment.BULLISH
            return Sentiment.NEUTRAL

        except Exception as e:
            print(f"Momentum check error for {figi}: {e}")
            return Sentiment.NEUTRAL

    # ── daily stats ───────────────────────────────────────────

    def get_daily_stats(self) -> dict:
        """Получить дневную статистику."""
        self._reset_daily_stats_if_needed()
        return {
            "daily_pnl": self._daily_pnl,
            "daily_trades": self._daily_trades,
            "remaining_loss_limit": self.limits.max_daily_loss_rub + self._daily_pnl
        }

