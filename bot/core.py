"""
Core — главный оркестратор бота.

Связывает все модули:
- validators: анализ Order Flow
- execution: исполнение заявок
- utils: логирование
- (позже) listeners: источники сигналов
"""

import asyncio
from datetime import datetime
from typing import Optional

from t_tech.invest import Client

from .config import Config
from .models import (
    ExitSignal,
    FlowAnalysis,
    NewsEvent,
    Position,
    PositionState,
    ReversalContext,
    Sentiment,
    ValidationResult,
)
from .signal_arbiter import ArbiterDecision, SignalArbiter
from .validators import FlowAnalyzer
from .execution import OrderManager, RiskManager, PositionTracker
from .utils import EventLogger, EventType


class TradingBot:
    """
    Главный класс бота.
    
    Поток данных:
    1. Event (из CLI, Telegram, etc.) -> validate()
    2. Если подтверждён -> execute()
    3. PositionTracker следит за SL/TP
    """
    
    def __init__(self, config: Config):
        self.config = config
        self._client_ctx: Optional[Client] = None  # Context manager
        self.client = None  # Services object (from __enter__)
        self.account_id: Optional[str] = None
        
        # Модули (инициализируются в start())
        self.flow_analyzer: Optional[FlowAnalyzer] = None
        self.risk_manager: Optional[RiskManager] = None
        self.position_tracker: Optional[PositionTracker] = None
        self.order_manager: Optional[OrderManager] = None
        self.signal_arbiter: Optional[SignalArbiter] = None
        
        # Логгер событий
        self.logger = EventLogger(console_output=False)  # Только в файл
        
        # Статистика
        self._events_received = 0
        self._events_validated = 0
        self._trades_executed = 0
    
    async def start(self):
        """Запуск бота."""
        print("\n" + "="*60)
        print("🚀 STARTING EVENT-DRIVEN TRADING BOT")
        print("="*60)
        
        # Подключаемся к API
        self._client_ctx = Client(self.config.tinkoff_token)
        self.client = self._client_ctx.__enter__()  # Returns Services object
        
        # Получаем/создаём аккаунт
        if self.config.use_sandbox:
            print("📦 Mode: SANDBOX")
            accounts = self.client.sandbox.get_sandbox_accounts()
            
            if accounts.accounts:
                self.account_id = accounts.accounts[0].id
                print(f"   Using existing account: {self.account_id}")
            else:
                response = self.client.sandbox.open_sandbox_account()
                self.account_id = response.account_id
                print(f"   Created new account: {self.account_id}")
                
                # Пополняем счёт
                from t_tech.invest import MoneyValue
                self.client.sandbox.sandbox_pay_in(
                    account_id=self.account_id,
                    amount=MoneyValue(units=1000000, nano=0, currency='rub')
                )
                print("   Deposited 1,000,000 RUB")
        else:
            print("💰 Mode: PRODUCTION")
            accounts = self.client.users.get_accounts()
            if accounts.accounts:
                self.account_id = accounts.accounts[0].id
                print(f"   Using account: {self.account_id}")
            else:
                raise RuntimeError("No trading accounts found")
        
        # Trade history database
        from .utils.trade_db import TradeDatabase
        self._trade_db = TradeDatabase(self.config.data_dir / "trades.db")
        self._trade_db.open()

        # Global API rate limiter
        from .utils.retry import AsyncRateLimiter
        self._rate_limiter = AsyncRateLimiter(calls_per_minute=180)

        # Инициализируем модули
        self.flow_analyzer = FlowAnalyzer(self.client, self.config)
        self.risk_manager = RiskManager(self.client, self.config)
        self.position_tracker = PositionTracker(
            self.client, self.config, self.risk_manager,
            trade_db=self._trade_db,
            rate_limiter=self._rate_limiter,
        )
        self.position_tracker.load_history()
        self.order_manager = OrderManager(
            self.client, self.config, self.account_id,
            self.risk_manager, self.position_tracker,
            rate_limiter=self._rate_limiter,
        )

        # Signal arbiter + unified exit callback
        self.signal_arbiter = SignalArbiter(
            dedup_window_sec=self.config.trading.dedup_window_sec,
        )
        self.position_tracker.on_exit(self._handle_exit)

        # Start position monitoring loop as a background task
        import asyncio as _asyncio
        self._monitor_task = _asyncio.create_task(
            self.position_tracker.monitor_loop()
        )

        # Reconcile with broker: recover positions from a previous session
        reconciled = await self._reconcile_positions()
        if reconciled:
            print(f"   Reconciled {reconciled} position(s) from broker")

        print(f"   Whitelist: {len(self.config.whitelist)} instruments")

        # Pre-warm trade data so baseline volume is available for validation
        figis = list(self.config.whitelist.values())
        print(f"   Warming up trade data for {len(figis)} instruments...")
        await self.flow_analyzer.warm_up(figis)

        print("="*60 + "\n")
        print("✅ Bot ready. Use process_event() or run CLI.")
    
    async def close_all_positions(self, reason: str = "SHUTDOWN") -> int:
        """Close all open positions. Returns count closed."""
        if not self.position_tracker or not self.order_manager:
            return 0
        positions = self.position_tracker.get_all_positions()
        closed = 0
        for pos in list(positions):
            try:
                if await self.order_manager.close_position_market(pos.figi, reason):
                    closed += 1
            except Exception as e:
                self.logger.log(
                    EventType.ERROR, ticker=pos.ticker,
                    reason=f"shutdown close failed: {e}",
                )
        return closed

    async def stop(self, close_positions: bool = False):
        """Остановка бота."""
        print("\n🛑 Stopping bot...")

        # 1. Stop monitoring (prevents new exit signals)
        if self.position_tracker:
            self.position_tracker.stop_monitoring()
        if hasattr(self, '_monitor_task') and self._monitor_task:
            self._monitor_task.cancel()
            try:
                import asyncio as _asyncio
                await _asyncio.wait_for(self._monitor_task, timeout=2.0)
            except Exception:
                pass

        # 2. Optionally close all open positions
        if close_positions:
            closed = await self.close_all_positions("SHUTDOWN")
            if closed:
                print(f"   Closed {closed} position(s)")

        # 3. Close trade database
        if hasattr(self, '_trade_db') and self._trade_db:
            self._trade_db.close()

        # 4. Close API client
        if hasattr(self, '_client_ctx') and self._client_ctx:
            self._client_ctx.__exit__(None, None, None)

        self._print_stats()
        print("👋 Bot stopped\n")
    
    async def process_event(
        self,
        event: NewsEvent,
        auto_execute: bool = True
    ) -> tuple[FlowAnalysis, Optional[Position]]:
        """
        Обработать событие.
        
        Args:
            event: Событие для обработки
            auto_execute: Автоматически исполнять подтверждённые сигналы
            
        Returns:
            (анализ, позиция или None)
        """
        self._events_received += 1

        if not self.config.is_allowed(event.ticker):
            self.logger.log(
                EventType.NEWS_IGNORED,
                ticker=event.ticker,
                reason="not in whitelist",
            )
            return self._empty_analysis(event.figi), None

        try:
            return await self._process_event_inner(event, auto_execute)
        except Exception as e:
            self.logger.log(
                EventType.ERROR,
                ticker=event.ticker,
                reason=f"process_event failed: {e}",
            )
            return self._empty_analysis(event.figi), None

    async def _process_event_inner(
        self,
        event: NewsEvent,
        auto_execute: bool,
    ) -> tuple[FlowAnalysis, Optional[Position]]:
        # ── Arbiter gate ───────────────────────────────────
        action = self.signal_arbiter.evaluate(
            event, self.position_tracker._positions,
        )

        if action.decision == ArbiterDecision.DUPLICATE:
            self.logger.log(
                EventType.SIGNAL_DUPLICATE,
                ticker=event.ticker,
                sentiment=event.sentiment.value,
                reason="duplicate within dedup window",
            )
            return self._empty_analysis(event.figi), None

        if action.decision == ArbiterDecision.CONTRADICTION:
            existing = action.existing_position
            price = await self.order_manager.get_last_price(event.figi) or 0.0
            ctx = ReversalContext(
                original_position=existing,
                reason="CONTRADICTION",
                reversed_sentiment=event.sentiment,
                current_price=price,
                figi=event.figi,
                ticker=event.ticker,
            )
            await self._attempt_reversal(ctx)
            return self._empty_analysis(event.figi), None

        # ── Order Flow validation ──────────────────────────
        analysis = await self.flow_analyzer.validate(event)

        position = None

        if analysis.validation_result == ValidationResult.CONFIRMED:
            self._events_validated += 1

            self.logger.signal_validated(
                ticker=event.ticker,
                sentiment=event.sentiment.value,
                confidence=analysis.confidence,
                imbalance=analysis.imbalance,
                volume_ratio=analysis.volume_ratio
            )

            if auto_execute:
                position = await self.order_manager.execute_signal(event, analysis)

                if position:
                    self._trades_executed += 1
                    self.logger.position_opened(
                        ticker=position.ticker,
                        sentiment=position.direction.value,
                        price=position.entry_price,
                        quantity=position.quantity,
                        stop_loss=position.stop_loss,
                        take_profit=position.take_profit or 0
                    )

        elif analysis.validation_result == ValidationResult.REJECTED:
            self.logger.signal_rejected(
                ticker=event.ticker,
                reason=f"OF: detected={analysis.detected_sentiment.value} "
                       f"imb={analysis.imbalance:+.2f} vol={analysis.volume_ratio:.1f}x"
            )

        else:
            self.logger.log(
                EventType.SIGNAL_INCONCLUSIVE,
                ticker=event.ticker,
                sentiment=event.sentiment.value,
                confidence=analysis.confidence,
                reason=f"imb={analysis.imbalance:+.2f} vol={analysis.volume_ratio:.1f}x"
            )

        return analysis, position
    
    def create_manual_event(
        self,
        ticker: str,
        sentiment: str,  # 'bullish', 'bearish', 'neutral'
        keywords: Optional[list[str]] = None
    ) -> Optional[NewsEvent]:
        """
        Создать событие вручную для тестирования.
        
        Args:
            ticker: Тикер (SBER, GAZP, etc.)
            sentiment: Направление сигнала
            keywords: Ключевые слова (опционально)
            
        Returns:
            NewsEvent или None если тикер не найден
        """
        figi = self.config.get_figi(ticker)
        if not figi:
            print(f"❌ Unknown ticker: {ticker}")
            return None
        
        sentiment_map = {
            'bullish': Sentiment.BULLISH,
            'bearish': Sentiment.BEARISH,
            'neutral': Sentiment.NEUTRAL,
            'buy': Sentiment.BULLISH,
            'sell': Sentiment.BEARISH,
            'long': Sentiment.BULLISH,
            'short': Sentiment.BEARISH,
        }
        
        sent = sentiment_map.get(sentiment.lower())
        if not sent:
            print(f"❌ Unknown sentiment: {sentiment}")
            return None
        
        return NewsEvent(
            ticker=ticker.upper(),
            figi=figi,
            sentiment=sent,
            confidence=1.0,
            keywords_found=keywords or [],
            timestamp=datetime.now(),
            source="manual"
        )
    
    # ── exit / reversal handling ────────────────────────────

    async def _handle_exit(self, signal: ExitSignal):
        """
        Unified callback from PositionTracker.

        - should_reverse=False → position already closed locally, just log.
        - should_reverse=True  → close + attempt reversal via OF.
        """
        if not signal.should_reverse:
            self.logger.position_closed(
                ticker=signal.ticker,
                price=signal.current_price,
                pnl=signal.position.pnl or 0.0,
                reason=signal.reason,
            )
            return

        reversed_sent = (
            Sentiment.BEARISH
            if signal.position.direction == Sentiment.BULLISH
            else Sentiment.BULLISH
        )
        ctx = ReversalContext(
            original_position=signal.position,
            reason=signal.reason,
            reversed_sentiment=reversed_sent,
            current_price=signal.current_price,
            figi=signal.figi,
            ticker=signal.ticker,
        )
        await self._attempt_reversal(ctx)

    async def _attempt_reversal(self, ctx: ReversalContext):
        """
        Shared reverse flow (used by both monitor exits and arbiter contradictions).

        1. Guard: skip if position is already REVERSING
        2. Set state = REVERSING
        3. Close position (rollback to ACTIVE on failure)
        4. Validate reversed direction via Order Flow
        5. If confirmed → open reverse position
        6. Otherwise → stay flat
        """
        pos = ctx.original_position

        if pos.state != PositionState.ACTIVE:
            return
        pos.state = PositionState.REVERSING

        try:
            closed = await self.order_manager.close_position_market(
                ctx.figi, ctx.reason,
            )
            if not closed:
                self.logger.log(
                    EventType.ORDER_FAILED,
                    ticker=ctx.ticker,
                    reason="close failed during reversal, rolling back to ACTIVE",
                )
                pos.state = PositionState.ACTIVE
                return

            self.logger.position_closed(
                ticker=ctx.ticker,
                price=ctx.current_price,
                pnl=pos.pnl or 0.0,
                reason=ctx.reason,
            )

            self.logger.log(
                EventType.POSITION_REVERSED,
                ticker=ctx.ticker,
                reason=f"validating reversal → {ctx.reversed_sentiment.value}",
            )
            analysis = await self.flow_analyzer.validate_reversal(ctx)

            if analysis.validation_result == ValidationResult.CONFIRMED:
                rev_event = self.create_manual_event(
                    ctx.ticker, ctx.reversed_sentiment.value,
                )
                if rev_event:
                    new_pos = await self.order_manager.execute_signal(rev_event, analysis)
                    if new_pos:
                        self._trades_executed += 1
                        self.logger.log(
                            EventType.POSITION_REVERSED,
                            ticker=ctx.ticker,
                            sentiment=ctx.reversed_sentiment.value,
                            price=new_pos.entry_price,
                            reason=ctx.reason,
                            details={
                                "prev_direction": ctx.original_position.direction.value,
                                "new_direction": ctx.reversed_sentiment.value,
                            },
                        )
                        self.logger.position_opened(
                            ticker=new_pos.ticker,
                            sentiment=new_pos.direction.value,
                            price=new_pos.entry_price,
                            quantity=new_pos.quantity,
                            stop_loss=new_pos.stop_loss,
                            take_profit=new_pos.take_profit or 0,
                        )
                        return

            self.logger.log(
                EventType.SIGNAL_REJECTED,
                ticker=ctx.ticker,
                reason="reversal not confirmed by OF — staying flat",
            )
        except Exception as e:
            self.logger.log(
                EventType.ERROR,
                ticker=ctx.ticker,
                reason=f"reversal failed unexpectedly: {e}",
            )
            if pos.figi in (self.position_tracker._positions or {}):
                pos.state = PositionState.ACTIVE

    # ── broker reconciliation ────────────────────────────

    async def _reconcile_positions(self) -> int:
        """Query broker portfolio and populate tracker with existing positions.
        Returns the number of positions recovered."""
        try:
            if self.config.use_sandbox:
                resp = self.client.sandbox.get_sandbox_portfolio(
                    account_id=self.account_id,
                )
            else:
                resp = self.client.operations.get_portfolio(
                    account_id=self.account_id,
                )
        except Exception as e:
            self.logger.log(EventType.ERROR, reason=f"portfolio reconciliation failed: {e}")
            return 0

        count = 0
        whitelist_figis = set(self.config.whitelist.values())

        for p in resp.positions:
            if p.instrument_type != "share":
                continue
            if p.figi not in whitelist_figis:
                continue

            qty_q = p.quantity
            qty = int(qty_q.units + qty_q.nano / 1e9)
            if qty == 0:
                continue

            avg_price_mv = p.average_position_price
            avg_price = avg_price_mv.units + avg_price_mv.nano / 1e9

            direction = Sentiment.BULLISH if qty > 0 else Sentiment.BEARISH
            abs_qty = abs(qty)

            atr = avg_price * 0.01  # conservative 1% fallback
            sl = self.risk_manager.calculate_stop_loss(avg_price, direction, atr)
            tp = self.risk_manager.calculate_take_profit(avg_price, direction, atr)

            pos = Position(
                figi=p.figi,
                ticker=p.ticker or p.figi,
                direction=direction,
                entry_price=avg_price,
                quantity=abs_qty,
                entry_time=datetime.now(),
                stop_loss=sl,
                take_profit=tp,
                atr=atr,
            )
            await self.position_tracker.add_position(pos)
            count += 1

        return count

    # ── helpers ───────────────────────────────────────────

    def _empty_analysis(self, figi: str) -> FlowAnalysis:
        """Пустой анализ для пропущенных событий."""
        return FlowAnalysis(
            figi=figi,
            timestamp=datetime.now(),
            imbalance=0.0,
            volume_ratio=0.0,
            price_change_percent=0.0,
            detected_sentiment=Sentiment.NEUTRAL,
            validation_result=ValidationResult.INCONCLUSIVE,
            confidence=0.0,
            details={"skipped": True}
        )
    
    def _print_stats(self):
        """Вывод статистики."""
        print("\n" + "="*50)
        print("SESSION STATISTICS")
        print("="*50)
        print(f"Events received:  {self._events_received}")
        print(f"Events validated: {self._events_validated}")
        print(f"Trades executed:  {self._trades_executed}")
        
        if self.position_tracker:
            stats = self.position_tracker.get_stats()
            print(f"\nTotal trades: {stats['total_trades']}")
            print(f"Win rate: {stats['win_rate']:.1%}")
            print(f"Total PnL: {stats['total_pnl']:+.2f} RUB")
        
        if self.risk_manager:
            daily = self.risk_manager.get_daily_stats()
            print(f"\nDaily PnL: {daily['daily_pnl']:+.2f} RUB")
        
        print("="*50)
    
    def get_status(self) -> dict:
        """Получить текущий статус бота."""
        return {
            "running": self.client is not None,
            "account_id": self.account_id,
            "sandbox": self.config.use_sandbox,
            "positions": self.position_tracker.get_positions_count() if self.position_tracker else 0,
            "events_received": self._events_received,
            "events_validated": self._events_validated,
            "trades_executed": self._trades_executed,
        }

