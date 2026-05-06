"""
Order Manager — выставление и управление заявками.
"""

import asyncio
import uuid
from datetime import datetime
from typing import Optional

from t_tech.invest import Client
from t_tech.invest import OrderDirection, OrderExecutionReportStatus, OrderType
from t_tech.invest import Quotation
from t_tech.invest.exceptions import RequestError

from ..config import Config
from ..models import FlowAnalysis, NewsEvent, Position, Sentiment
from ..utils.retry import async_retry
from .risk_manager import RiskManager
from .position_tracker import PositionTracker


class OrderManager:
    """
    Управление заявками.
    """
    
    def __init__(
        self,
        client: Client,
        config: Config,
        account_id: str,
        risk_manager: RiskManager,
        position_tracker: PositionTracker,
        rate_limiter=None,
    ):
        self.client = client
        self.config = config
        self.account_id = account_id
        self.risk_manager = risk_manager
        self.position_tracker = position_tracker
        self._rate_limiter = rate_limiter
        self._instrument_cache: dict[str, dict] = {}

        # Используем sandbox если включен
        self.use_sandbox = config.use_sandbox
    
    def _float_to_quotation(self, value: float) -> Quotation:
        """Конвертация float в Quotation."""
        units = int(value)
        nano = int((value - units) * 1e9)
        return Quotation(units=units, nano=nano)
    
    def _quotation_to_float(self, quotation) -> float:
        """Конвертация Quotation в float."""
        return quotation.units + quotation.nano / 1e9
    
    @async_retry(max_attempts=3, base_delay=0.5, max_delay=5.0)
    async def _get_last_price_api(self, figi: str) -> Optional[float]:
        if self._rate_limiter:
            await self._rate_limiter.acquire()
        response = self.client.market_data.get_last_prices(figi=[figi])
        if response.last_prices:
            return self._quotation_to_float(response.last_prices[0].price)
        return None

    async def get_last_price(self, figi: str) -> Optional[float]:
        """Получить последнюю цену. Retries up to 3 times."""
        try:
            return await self._get_last_price_api(figi)
        except Exception as e:
            print(f"Get price error: {e}")
        return None

    @async_retry(max_attempts=3, base_delay=0.5, max_delay=5.0)
    async def _get_instrument_info_api(self, figi: str) -> dict:
        if self._rate_limiter:
            await self._rate_limiter.acquire()
        response = self.client.instruments.get_instrument_by(
            id_type=1, id=figi
        )
        return {
            "figi": response.instrument.figi,
            "ticker": response.instrument.ticker,
            "name": response.instrument.name,
            "lot": response.instrument.lot,
            "currency": response.instrument.currency,
            "min_price_increment": self._quotation_to_float(
                response.instrument.min_price_increment
            ),
        }

    async def get_instrument_info(self, figi: str) -> Optional[dict]:
        """Получить информацию об инструменте. Cached after first call."""
        cached = self._instrument_cache.get(figi)
        if cached is not None:
            return cached
        try:
            info = await self._get_instrument_info_api(figi)
            if info is not None:
                self._instrument_cache[figi] = info
            return info
        except Exception as e:
            print(f"Get instrument error: {e}")
        return None
    
    def _calculate_marketable_limit(
        self,
        last_price: float,
        direction: Sentiment,
        slippage_percent: float = 0.1
    ) -> float:
        """
        Рассчитать цену Marketable Limit Order.
        
        Marketable Limit = рыночная цена + небольшой запас.
        Это позволяет исполниться быстро, но с защитой от сильного проскальзывания.
        """
        slippage = last_price * (slippage_percent / 100)
        
        if direction == Sentiment.BULLISH:
            # Покупка: цена чуть выше рынка
            return last_price + slippage
        elif direction == Sentiment.BEARISH:
            # Продажа: цена чуть ниже рынка
            return last_price - slippage
        else:
            return last_price
    
    async def execute_signal(
        self,
        event: NewsEvent,
        analysis: FlowAnalysis
    ) -> Optional[Position]:
        """
        Исполнить торговый сигнал.
        
        Args:
            event: Событие (новость или ручной сигнал)
            analysis: Результат анализа Order Flow
            
        Returns:
            Открытая позиция или None если не удалось
        """
        figi = event.figi
        direction = event.sentiment
        
        # Проверяем направление
        if direction == Sentiment.NEUTRAL:
            print("⚪ Signal is NEUTRAL — no action")
            return None

        # Проверяем, нет ли уже позиции
        if self.position_tracker.has_position(figi):
            print(f"⚠️ Already have position in {event.ticker}")
            return None

        # Получаем цену
        last_price = await self.get_last_price(figi)
        if last_price is None:
            print(f"❌ Cannot get price for {event.ticker}")
            return None

        # Получаем информацию об инструменте
        info = await self.get_instrument_info(figi)
        if info is None:
            print(f"❌ Cannot get instrument info for {event.ticker}")
            return None

        lot_size = info.get("lot", 1)
        ticker = info.get("ticker", event.ticker)

        # Рассчитываем размер позиции
        quantity = self.risk_manager.calculate_position_size(
            price=last_price,
            lot_size=lot_size
        )

        if quantity <= 0:
            print(f"❌ Position size is 0 for {ticker} (price={last_price}, lot={lot_size})")
            return None

        # Проверяем лимиты
        can_open, reason = self.risk_manager.can_open_position(
            price=last_price * lot_size,
            quantity=quantity,
            current_positions=self.position_tracker.get_positions_count()
        )

        if not can_open:
            print(f"❌ Cannot open position: {reason}")
            return None

        print(f"📊 PRE-FLIGHT OK: {ticker} {direction.value} price={last_price:.2f} qty={quantity} lots")
        
        # Рассчитываем ATR для Stop-Loss
        atr = await self.risk_manager.calculate_atr(figi)
        if atr <= 0:
            # Fallback: 1% от цены
            atr = last_price * 0.01
            print(f"⚠️ Using fallback ATR: {atr:.2f}")
        
        # Рассчитываем SL и TP
        stop_loss = self.risk_manager.calculate_stop_loss(last_price, direction, atr)
        take_profit = self.risk_manager.calculate_take_profit(last_price, direction, atr)
        
        # Цена заявки (Marketable Limit)
        limit_price = self._calculate_marketable_limit(last_price, direction)
        
        # Направление заявки
        order_direction = (
            OrderDirection.ORDER_DIRECTION_BUY
            if direction == Sentiment.BULLISH
            else OrderDirection.ORDER_DIRECTION_SELL
        )
        
        print(f"\n{'='*50}")
        print(f"📋 EXECUTING ORDER: {ticker}")
        print(f"   Direction: {direction.value}")
        print(f"   Price: {last_price:.2f} (limit: {limit_price:.2f})")
        print(f"   Quantity: {quantity} lots ({quantity * lot_size} shares)")
        print(f"   ATR: {atr:.2f}")
        print(f"   SL: {stop_loss:.2f} | TP: {take_profit:.2f}")
        print(f"{'='*50}\n")

        try:
            response = await self._submit_order(
                figi, quantity, limit_price, order_direction
            )

            status = response.execution_report_status
            print(f"✅ Order placed: {response.order_id}")
            print(f"   Status: {status}")

            _REJECTED = OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_REJECTED
            _CANCELLED = OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_CANCELLED
            _FILL = OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_FILL
            _PARTIAL = OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_PARTIALLYFILL

            if status in (_REJECTED, _CANCELLED):
                msg = getattr(response, "message", "")
                print(f"❌ Order {status.name}: {msg}")
                return None

            executed_lots = response.lots_executed or quantity
            actual_price = last_price
            ep = getattr(response, "executed_order_price", None)
            if ep is not None:
                avg = ep.units + ep.nano / 1e9
                if avg > 0:
                    actual_price = avg

            if status not in (_FILL, _PARTIAL):
                print(f"⚠️ Order status {status.name} — not filled yet, skipping position")
                return None

            position = Position(
                figi=figi,
                ticker=ticker,
                direction=direction,
                entry_price=actual_price,
                quantity=executed_lots * lot_size,
                entry_time=datetime.now(),
                stop_loss=stop_loss,
                take_profit=take_profit,
                atr=atr
            )

            await self.position_tracker.add_position(position)

            return position

        except RequestError as e:
            print(f"❌ Order error: {e.metadata.message if hasattr(e, 'metadata') else e}")
            return None
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            return None

    @async_retry(
        max_attempts=2,
        base_delay=1.0,
        max_delay=5.0,
        retryable_exceptions=(ConnectionError, asyncio.TimeoutError, OSError),
    )
    async def _submit_order(self, figi, quantity, limit_price, order_direction):
        """Submit an order to the broker. Retries only on network errors (2 attempts max)."""
        if self.use_sandbox:
            return self.client.sandbox.post_sandbox_order(
                account_id=self.account_id,
                figi=figi,
                quantity=quantity,
                price=self._float_to_quotation(limit_price),
                direction=order_direction,
                order_type=OrderType.ORDER_TYPE_LIMIT,
                order_id=str(uuid.uuid4()),
            )
        else:
            return self.client.orders.post_order(
                account_id=self.account_id,
                figi=figi,
                quantity=quantity,
                price=self._float_to_quotation(limit_price),
                direction=order_direction,
                order_type=OrderType.ORDER_TYPE_LIMIT,
                order_id=str(uuid.uuid4()),
            )
    
    async def close_position_market(
        self,
        figi: str,
        reason: str = "MANUAL"
    ) -> bool:
        """
        Закрыть позицию рыночной заявкой.
        
        Args:
            figi: FIGI инструмента
            reason: Причина закрытия
            
        Returns:
            True если успешно
        """
        position = self.position_tracker.get_position(figi)
        
        if position is None:
            print(f"⚠️ No position to close for {figi}")
            return False
        
        # Получаем цену
        last_price = await self.get_last_price(figi)
        if last_price is None:
            print(f"❌ Cannot get price for closing")
            return False
        
        # Информация об инструменте для лотов
        info = await self.get_instrument_info(figi)
        lot_size = info.get("lot", 1) if info else 1
        
        # Количество лотов
        quantity_lots = position.quantity // lot_size
        
        # Направление: противоположное позиции
        if position.direction == Sentiment.BULLISH:
            order_direction = OrderDirection.ORDER_DIRECTION_SELL
        else:
            order_direction = OrderDirection.ORDER_DIRECTION_BUY
        
        try:
            response = await self._submit_close_order(
                figi, quantity_lots, last_price, order_direction
            )

            status = response.execution_report_status
            _REJECTED = OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_REJECTED
            _CANCELLED = OrderExecutionReportStatus.EXECUTION_REPORT_STATUS_CANCELLED

            if status in (_REJECTED, _CANCELLED):
                msg = getattr(response, "message", "")
                print(f"❌ Close order {status.name}: {msg}")
                return False

            exit_price = last_price
            ep = getattr(response, "executed_order_price", None)
            if ep is not None:
                avg = ep.units + ep.nano / 1e9
                if avg > 0:
                    exit_price = avg

            print(f"✅ Close order placed: {response.order_id}")

            await self.position_tracker.close_position(figi, exit_price, reason)

            return True

        except RequestError as e:
            print(f"❌ Close order error: {e.metadata.message if hasattr(e, 'metadata') else e}")
            return False
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            return False

    @async_retry(
        max_attempts=3,
        base_delay=1.0,
        max_delay=5.0,
        retryable_exceptions=(ConnectionError, asyncio.TimeoutError, OSError),
    )
    async def _submit_close_order(self, figi, quantity_lots, price, order_direction):
        """Submit a close order. 3 attempts — failing to close is riskier than a duplicate."""
        if self.use_sandbox:
            return self.client.sandbox.post_sandbox_order(
                account_id=self.account_id,
                figi=figi,
                quantity=quantity_lots,
                price=self._float_to_quotation(price),
                direction=order_direction,
                order_type=OrderType.ORDER_TYPE_MARKET,
                order_id=str(uuid.uuid4()),
            )
        else:
            return self.client.orders.post_order(
                account_id=self.account_id,
                figi=figi,
                quantity=quantity_lots,
                price=self._float_to_quotation(price),
                direction=order_direction,
                order_type=OrderType.ORDER_TYPE_MARKET,
                order_id=str(uuid.uuid4()),
            )
    
    async def cancel_order(self, order_id: str) -> bool:
        """Отменить заявку."""
        try:
            if self.use_sandbox:
                self.client.sandbox.cancel_sandbox_order(
                    account_id=self.account_id,
                    order_id=order_id
                )
            else:
                self.client.orders.cancel_order(
                    account_id=self.account_id,
                    order_id=order_id
                )
            print(f"✅ Order {order_id} cancelled")
            return True
        except Exception as e:
            print(f"❌ Cancel error: {e}")
            return False

