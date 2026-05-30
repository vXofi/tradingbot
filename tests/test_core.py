"""Tests for bot.core — TradingBot orchestration with mocked dependencies."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core import TradingBot
from bot.models import (
    ExitSignal,
    FlowAnalysis,
    PositionState,
    Sentiment,
    ValidationResult,
)
from bot.signal_arbiter import SignalArbiter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _flow_analysis(
    figi: str,
    result: ValidationResult,
    sentiment: Sentiment = Sentiment.BULLISH,
    confidence: float = 0.8,
) -> FlowAnalysis:
    return FlowAnalysis(
        figi=figi,
        timestamp=datetime.now(),
        imbalance=0.5 if sentiment == Sentiment.BULLISH else -0.5,
        volume_ratio=4.0,
        price_change_percent=0.3,
        detected_sentiment=sentiment,
        validation_result=result,
        confidence=confidence,
        details={},
    )


@pytest.fixture()
def wired_bot(mock_config):
    """TradingBot with arbiter and mocked execution/validation modules."""
    bot = TradingBot(mock_config)
    bot.signal_arbiter = SignalArbiter(dedup_window_sec=60.0)
    bot.position_tracker = MagicMock()
    bot.position_tracker._positions = {}
    bot.flow_analyzer = AsyncMock()
    bot.order_manager = AsyncMock()
    return bot


# ===========================================================================
# process_event
# ===========================================================================


class TestProcessEvent:
    @pytest.mark.asyncio
    async def test_whitelist_rejects_unknown_ticker(self, wired_bot, make_news_event):
        event = make_news_event(ticker="ZZZZ", figi="figi-unknown")
        analysis, position = await wired_bot.process_event(event)
        assert position is None
        assert analysis.details.get("skipped") is True
        wired_bot.flow_analyzer.validate.assert_not_called()

    @pytest.mark.asyncio
    async def test_duplicate_dropped(self, wired_bot, make_news_event):
        event = make_news_event()
        wired_bot.flow_analyzer.validate.return_value = _flow_analysis(
            event.figi, ValidationResult.INCONCLUSIVE,
        )

        await wired_bot.process_event(event, auto_execute=False)
        analysis, position = await wired_bot.process_event(event, auto_execute=False)

        assert position is None
        assert analysis.details.get("skipped") is True
        assert wired_bot.flow_analyzer.validate.call_count == 1

    @pytest.mark.asyncio
    async def test_confirmed_signal_executes(
        self, wired_bot, make_news_event, make_position,
    ):
        event = make_news_event()
        pos = make_position()
        wired_bot.flow_analyzer.validate.return_value = _flow_analysis(
            event.figi, ValidationResult.CONFIRMED,
        )
        wired_bot.order_manager.execute_signal.return_value = pos

        analysis, position = await wired_bot.process_event(event)

        assert analysis.validation_result == ValidationResult.CONFIRMED
        assert position is pos
        wired_bot.order_manager.execute_signal.assert_awaited_once()
        assert wired_bot._trades_executed == 1
        assert wired_bot._events_validated == 1

    @pytest.mark.asyncio
    async def test_confirmed_without_auto_execute(
        self, wired_bot, make_news_event,
    ):
        event = make_news_event()
        wired_bot.flow_analyzer.validate.return_value = _flow_analysis(
            event.figi, ValidationResult.CONFIRMED,
        )

        analysis, position = await wired_bot.process_event(event, auto_execute=False)

        assert analysis.validation_result == ValidationResult.CONFIRMED
        assert position is None
        wired_bot.order_manager.execute_signal.assert_not_called()
        assert wired_bot._events_validated == 1

    @pytest.mark.asyncio
    async def test_rejected_signal_no_execute(self, wired_bot, make_news_event):
        event = make_news_event()
        wired_bot.flow_analyzer.validate.return_value = _flow_analysis(
            event.figi, ValidationResult.REJECTED, Sentiment.BEARISH,
        )

        analysis, position = await wired_bot.process_event(event)

        assert analysis.validation_result == ValidationResult.REJECTED
        assert position is None
        wired_bot.order_manager.execute_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_contradiction_triggers_reversal(
        self, wired_bot, make_news_event, make_position,
    ):
        pos = make_position(direction=Sentiment.BULLISH, state=PositionState.ACTIVE)
        wired_bot.position_tracker._positions = {pos.figi: pos}
        event = make_news_event(sentiment=Sentiment.BEARISH)
        wired_bot.order_manager.get_last_price.return_value = 101.0

        with patch.object(
            wired_bot, "_attempt_reversal", new_callable=AsyncMock,
        ) as mock_rev:
            analysis, position = await wired_bot.process_event(event)

        assert position is None
        assert analysis.details.get("skipped") is True
        mock_rev.assert_awaited_once()
        wired_bot.flow_analyzer.validate.assert_not_called()


# ===========================================================================
# exit / manual helpers
# ===========================================================================


class TestExitAndHelpers:
    @pytest.mark.asyncio
    async def test_handle_exit_without_reverse_logs_only(
        self, wired_bot, make_position,
    ):
        pos = make_position()
        signal = ExitSignal(
            figi=pos.figi,
            ticker=pos.ticker,
            reason="STOP_LOSS",
            current_price=90.0,
            should_reverse=False,
            position=pos,
        )

        with patch.object(wired_bot.logger, "position_closed") as mock_log:
            await wired_bot._handle_exit(signal)

        mock_log.assert_called_once()
        wired_bot.flow_analyzer.validate_reversal.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_exit_with_reverse_attempts_reversal(
        self, wired_bot, make_position,
    ):
        pos = make_position(direction=Sentiment.BULLISH)
        signal = ExitSignal(
            figi=pos.figi,
            ticker=pos.ticker,
            reason="TRAILING_STOP",
            current_price=105.0,
            should_reverse=True,
            position=pos,
        )

        with patch.object(
            wired_bot, "_attempt_reversal", new_callable=AsyncMock,
        ) as mock_rev:
            await wired_bot._handle_exit(signal)

        mock_rev.assert_awaited_once()

    def test_create_manual_event(self, wired_bot):
        event = wired_bot.create_manual_event("SBER", "bullish", ["дивиденды"])
        assert event is not None
        assert event.ticker == "SBER"
        assert event.sentiment == Sentiment.BULLISH
        assert event.source == "manual"

    def test_create_manual_event_unknown_ticker(self, wired_bot):
        assert wired_bot.create_manual_event("NOPE", "bullish") is None

    def test_get_status_before_start(self, wired_bot):
        status = wired_bot.get_status()
        assert status["running"] is False
        assert status["sandbox"] is True
        assert status["events_received"] == 0
