"""
Анализатор Order Flow — валидация сигналов.
"""

import asyncio
import time as _time
from datetime import datetime
from typing import Optional

from t_tech.invest import Client

from ..config import Config
from ..models import (
    FlowAnalysis,
    NewsEvent,
    ReversalContext,
    Sentiment,
    ValidationResult,
)
from .orderbook_stream import OrderBookStream
from .trades_stream import TradesStream

_TRADE_FETCH_COOLDOWN = 5.0  # seconds between REST trade fetches per figi


class FlowAnalyzer:
    """
    Главный анализатор Order Flow.
    Валидирует сигналы через стакан и ленту сделок.
    """
    
    def __init__(self, client: Client, config: Config):
        self.client = client
        self.config = config
        
        self.orderbook = OrderBookStream(client)
        self.trades = TradesStream(client)

        self._last_trade_fetch: dict[str, float] = {}  # figi → monotonic time

    async def warm_up(self, figis: list[str]):
        """Pre-fetch recent trades for a list of FIGIs so baseline volume is ready."""
        for figi in figis:
            try:
                await self.trades.fetch_last_trades(figi, minutes=5)
                self._last_trade_fetch[figi] = _time.monotonic()
            except Exception as e:
                print(f"  warm-up error for {figi}: {e}")
            await asyncio.sleep(0.3)  # rate-limit friendly

    async def _ensure_trade_data(self, figi: str):
        """Fetch recent trades via REST if not loaded recently."""
        now = _time.monotonic()
        if now - self._last_trade_fetch.get(figi, 0.0) < _TRADE_FETCH_COOLDOWN:
            return
        self._last_trade_fetch[figi] = now
        await self.trades.fetch_last_trades(figi, minutes=5)

    async def analyze(self, figi: str) -> FlowAnalysis:
        """
        Анализ текущего состояния Order Flow.
        
        Возвращает анализ без привязки к конкретному сигналу.
        """
        await self.orderbook.poll(figi)
        await self._ensure_trade_data(figi)
        
        imbalance = self.orderbook.calculate_imbalance(figi) or 0.0

        # Use 30s window for volume analysis — 1s only works with live streams,
        # REST-fetched data needs a wider window to capture recent activity.
        _VOL_WINDOW = 30.0

        is_spike, volume_ratio = self.trades.detect_volume_spike(
            figi,
            threshold=self.config.validation.volume_spike_threshold,
            window_seconds=_VOL_WINDOW,
        )

        price_change = self.trades.get_price_change(figi, seconds=_VOL_WINDOW) or 0.0

        orderbook_sentiment = self.orderbook.detect_sentiment_from_imbalance(
            figi,
            threshold=self.config.validation.imbalance_threshold
        )
        trades_sentiment = self.trades.detect_sentiment_from_trades(
            figi,
            seconds=_VOL_WINDOW,
            threshold=self.config.validation.imbalance_threshold
        )
        
        detected_sentiment = self._combine_sentiments(
            orderbook_sentiment,
            trades_sentiment,
            price_change
        )
        
        directional_agree = (
            orderbook_sentiment == trades_sentiment
            and orderbook_sentiment != Sentiment.NEUTRAL
        )
        confidence = self._calculate_confidence(
            imbalance=imbalance,
            volume_ratio=volume_ratio,
            price_change=price_change,
            directional_agree=directional_agree,
        )
        
        if confidence >= self.config.validation.min_confidence:
            if detected_sentiment == Sentiment.NEUTRAL:
                validation = ValidationResult.INCONCLUSIVE
            else:
                validation = ValidationResult.CONFIRMED
        else:
            validation = ValidationResult.INCONCLUSIVE
        
        return FlowAnalysis(
            figi=figi,
            timestamp=datetime.now(),
            imbalance=imbalance,
            volume_ratio=volume_ratio,
            price_change_percent=price_change,
            detected_sentiment=detected_sentiment,
            validation_result=validation,
            confidence=confidence,
            details={
                "orderbook_sentiment": orderbook_sentiment.value,
                "trades_sentiment": trades_sentiment.value,
                "is_volume_spike": is_spike,
            }
        )
    
    async def validate(
        self,
        event: NewsEvent,
        timeout: Optional[float] = None
    ) -> FlowAnalysis:
        """
        Валидация сигнала из новости.

        Ждём подтверждения от Order Flow в течение timeout секунд.
        Для NEUTRAL события — просто анализируем текущее состояние.
        """
        if event.sentiment == Sentiment.NEUTRAL:
            return await self.analyze(event.figi)

        return await self._validate_direction(
            figi=event.figi,
            expected=event.sentiment,
            timeout=timeout,
        )

    async def validate_reversal(
        self,
        ctx: ReversalContext,
        timeout: Optional[float] = None,
    ) -> FlowAnalysis:
        """
        Валидация разворота через Order Flow.

        Принимает ReversalContext (а не NewsEvent) — не создаёт фейковых объектов.
        """
        return await self._validate_direction(
            figi=ctx.figi,
            expected=ctx.reversed_sentiment,
            timeout=timeout,
        )

    # ── shared validation loop ──────────────────────────────

    async def _validate_direction(
        self,
        figi: str,
        expected: Sentiment,
        timeout: Optional[float] = None,
    ) -> FlowAnalysis:
        """
        Core polling loop shared by validate() and validate_reversal().

        Polls Order Flow for *timeout* seconds looking for confirmation that
        the market is moving in the *expected* direction.
        """
        timeout = timeout or self.config.validation.reaction_window_sec

        start_time = asyncio.get_event_loop().time()
        best_analysis: Optional[FlowAnalysis] = None

        consecutive_errors = 0
        _MAX_CONSECUTIVE_ERRORS = 3

        while asyncio.get_event_loop().time() - start_time < timeout:
            try:
                analysis = await self.analyze(figi)
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                print(f"FlowAnalyzer error ({consecutive_errors}/{_MAX_CONSECUTIVE_ERRORS}): {e}")
                if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                    break
                await asyncio.sleep(0.2)
                continue

            if self._confirms_signal(analysis, expected):
                analysis.validation_result = ValidationResult.CONFIRMED
                return analysis

            if best_analysis is None or analysis.confidence > best_analysis.confidence:
                best_analysis = analysis

            await asyncio.sleep(0.2)

        if best_analysis:
            min_conf = self.config.validation.min_confidence
            if (
                best_analysis.detected_sentiment == expected
                and best_analysis.confidence >= min_conf
            ):
                best_analysis.validation_result = ValidationResult.CONFIRMED
            elif best_analysis.detected_sentiment == Sentiment.NEUTRAL:
                best_analysis.validation_result = ValidationResult.INCONCLUSIVE
            elif best_analysis.detected_sentiment != expected:
                best_analysis.validation_result = ValidationResult.REJECTED
            else:
                best_analysis.validation_result = ValidationResult.INCONCLUSIVE
            return best_analysis

        return FlowAnalysis(
            figi=figi,
            timestamp=datetime.now(),
            imbalance=0.0,
            volume_ratio=0.0,
            price_change_percent=0.0,
            detected_sentiment=Sentiment.NEUTRAL,
            validation_result=ValidationResult.INCONCLUSIVE,
            confidence=0.0,
            details={"error": "no_data"},
        )
    
    def _combine_sentiments(
        self,
        orderbook_sentiment: Sentiment,
        trades_sentiment: Sentiment,
        price_change: float
    ) -> Sentiment:
        """
        Комбинирование сигналов из разных источников.
        
        Non-neutral sources vote; a single directional source is enough
        (confidence scoring handles weight).  Two conflicting directional
        sources cancel to NEUTRAL.
        """
        price_sentiment = Sentiment.NEUTRAL
        if price_change > self.config.validation.price_move_threshold:
            price_sentiment = Sentiment.BULLISH
        elif price_change < -self.config.validation.price_move_threshold:
            price_sentiment = Sentiment.BEARISH
        
        non_neutral = [
            s for s in (orderbook_sentiment, trades_sentiment, price_sentiment)
            if s != Sentiment.NEUTRAL
        ]
        
        if not non_neutral:
            return Sentiment.NEUTRAL
        
        bullish = sum(1 for s in non_neutral if s == Sentiment.BULLISH)
        bearish = sum(1 for s in non_neutral if s == Sentiment.BEARISH)
        
        if bullish > 0 and bearish == 0:
            return Sentiment.BULLISH
        if bearish > 0 and bullish == 0:
            return Sentiment.BEARISH
        return Sentiment.NEUTRAL
    
    def _calculate_confidence(
        self,
        imbalance: float,
        volume_ratio: float,
        price_change: float,
        directional_agree: bool,
    ) -> float:
        """
        Расчёт уверенности в сигнале (0.0 - 1.0).

        `directional_agree` should only be True when both orderbook and trades
        express the *same non-neutral* sentiment.
        """
        confidence = 0.0
        
        # Сила дисбаланса (0.0 - 0.3)
        confidence += min(abs(imbalance), 1.0) * 0.3
        
        # Всплеск объёма (0.0 - 0.3)
        if volume_ratio >= self.config.validation.volume_spike_threshold:
            normalized = min(volume_ratio / (self.config.validation.volume_spike_threshold * 2), 1.0)
            confidence += normalized * 0.3
        
        # Движение цены (0.0 - 0.2)
        price_factor = min(abs(price_change) / 1.0, 1.0)
        confidence += price_factor * 0.2
        
        # Directional agreement bonus (0.0 - 0.2) — only when both sources
        # are non-neutral and match; neutral+neutral does NOT count.
        if directional_agree:
            confidence += 0.2
        
        return min(confidence, 1.0)
    
    def _confirms_signal(
        self,
        analysis: FlowAnalysis,
        expected: Sentiment
    ) -> bool:
        """
        Проверка, подтверждает ли анализ ожидаемый сигнал.
        """
        if expected == Sentiment.NEUTRAL:
            return True  # Neutral всегда подтверждается
        
        return (
            analysis.detected_sentiment == expected and
            analysis.confidence >= self.config.validation.min_confidence
        )
    
    async def get_market_state(self, figi: str) -> dict:
        """
        Получить текущее состояние рынка для отладки.
        """
        analysis = await self.analyze(figi)
        
        snapshot = self.orderbook.get_latest(figi)
        
        return {
            "figi": figi,
            "timestamp": datetime.now().isoformat(),
            "orderbook": {
                "best_bid": snapshot.best_bid if snapshot else None,
                "best_ask": snapshot.best_ask if snapshot else None,
                "spread": snapshot.spread if snapshot else None,
                "imbalance": analysis.imbalance,
            },
            "trades": {
                "volume_1s": self.trades.get_volume(figi, 1.0),
                "volume_5s": self.trades.get_volume(figi, 5.0),
                "buy_volume_1s": self.trades.get_buy_volume(figi, 1.0),
                "sell_volume_1s": self.trades.get_sell_volume(figi, 1.0),
                "baseline_per_sec": self.trades.get_baseline_volume(figi),
                "volume_ratio": analysis.volume_ratio,
            },
            "analysis": {
                "detected_sentiment": analysis.detected_sentiment.value,
                "confidence": analysis.confidence,
                "price_change_percent": analysis.price_change_percent,
            }
        }

