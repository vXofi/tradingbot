"""End-to-end pipeline test: parse → arbiter → flow → execute (all mocked except parser)."""

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core import TradingBot
from bot.listeners.news_parser import NewsParser
from bot.models import FlowAnalysis, Sentiment, ValidationResult
from bot.signal_arbiter import SignalArbiter


@pytest.fixture()
def parser():
    return NewsParser(data_dir=Path("data"), use_llm_fallback=False)


@pytest.fixture()
def pipeline_bot(mock_config, parser):
    bot = TradingBot(mock_config)
    bot.signal_arbiter = SignalArbiter(dedup_window_sec=60.0)
    bot.position_tracker = MagicMock()
    bot.position_tracker._positions = {}
    bot.flow_analyzer = AsyncMock()
    bot.order_manager = AsyncMock()
    bot.parser = parser
    return bot


def _confirmed_analysis(figi: str, sentiment: Sentiment) -> FlowAnalysis:
    return FlowAnalysis(
        figi=figi,
        timestamp=datetime.now(),
        imbalance=0.4 if sentiment == Sentiment.BULLISH else -0.4,
        volume_ratio=5.0,
        price_change_percent=0.5,
        detected_sentiment=sentiment,
        validation_result=ValidationResult.CONFIRMED,
        confidence=0.85,
        details={},
    )


class TestE2EPipeline:
    @pytest.mark.asyncio
    async def test_news_to_sandbox_order(self, pipeline_bot, make_position):
        """Headline → NewsParser → process_event → mock order execution."""
        headline = "У Сбербанка прибыль выросла на 30% за квартал"
        parse_result = pipeline_bot.parser.parse(headline)
        assert parse_result.success is True
        assert "SBER" in parse_result.tickers

        events = pipeline_bot.parser.to_news_events(parse_result, source="eval")
        assert len(events) >= 1
        event = events[0]

        pos = make_position(ticker="SBER", figi=event.figi)
        pipeline_bot.flow_analyzer.validate.return_value = _confirmed_analysis(
            event.figi, event.sentiment,
        )
        pipeline_bot.order_manager.execute_signal.return_value = pos

        analysis, position = await pipeline_bot.process_event(event)

        assert analysis.validation_result == ValidationResult.CONFIRMED
        assert position is pos
        assert pipeline_bot._events_received == 1
        assert pipeline_bot._events_validated == 1
        assert pipeline_bot._trades_executed == 1
        pipeline_bot.flow_analyzer.validate.assert_awaited_once()
        pipeline_bot.order_manager.execute_signal.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_news_rejected_by_order_flow(self, pipeline_bot):
        headline = "Газпром зафиксировал крупный убыток по итогам года"
        event = pipeline_bot.parser.to_news_events(
            pipeline_bot.parser.parse(headline), source="eval",
        )[0]

        pipeline_bot.flow_analyzer.validate.return_value = FlowAnalysis(
            figi=event.figi,
            timestamp=datetime.now(),
            imbalance=0.3,
            volume_ratio=1.0,
            price_change_percent=0.1,
            detected_sentiment=Sentiment.BULLISH,
            validation_result=ValidationResult.REJECTED,
            confidence=0.2,
            details={},
        )

        analysis, position = await pipeline_bot.process_event(event)

        assert analysis.validation_result == ValidationResult.REJECTED
        assert position is None
        pipeline_bot.order_manager.execute_signal.assert_not_called()
