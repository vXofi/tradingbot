"""Tests for bot.utils.event_logger — structured event logging with subscribers."""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import pytest

from bot.utils.event_logger import EventLogger, EventType, LogEvent


# ===========================================================================
# EventType enum
# ===========================================================================


class TestEventType:
    """Verify key enum members exist and carry expected string values."""

    def test_news_parsed(self):
        assert EventType.NEWS_PARSED.value == "news_parsed"

    def test_signal_validated(self):
        assert EventType.SIGNAL_VALIDATED.value == "signal_validated"

    def test_order_placed(self):
        assert EventType.ORDER_PLACED.value == "order_placed"

    def test_position_opened(self):
        assert EventType.POSITION_OPENED.value == "position_opened"

    def test_position_closed(self):
        assert EventType.POSITION_CLOSED.value == "position_closed"

    def test_stop_loss_hit(self):
        assert EventType.STOP_LOSS_HIT.value == "stop_loss_hit"

    def test_error(self):
        assert EventType.ERROR.value == "error"

    def test_stream_reconnect(self):
        assert EventType.STREAM_RECONNECT.value == "stream_reconnect"

    def test_api_retry(self):
        assert EventType.API_RETRY.value == "api_retry"

    def test_rate_limited(self):
        assert EventType.RATE_LIMITED.value == "rate_limited"


# ===========================================================================
# LogEvent dataclass
# ===========================================================================


class TestLogEvent:
    """LogEvent creation, defaults, and to_dict serialisation."""

    def test_creation_with_defaults(self):
        event = LogEvent(
            timestamp="2026-04-10T12:00:00",
            event_type="news_parsed",
        )
        assert event.ticker is None
        assert event.figi is None
        assert event.sentiment is None
        assert event.confidence is None
        assert event.price is None
        assert event.quantity is None
        assert event.pnl is None
        assert event.reason is None
        assert event.details == {}

    def test_creation_all_fields(self):
        event = LogEvent(
            timestamp="2026-04-10T12:00:00",
            event_type="order_placed",
            ticker="SBER",
            figi="BBG004730N88",
            sentiment="bullish",
            confidence=0.85,
            price=250.50,
            quantity=10,
            pnl=120.0,
            reason="strong signal",
            details={"extra": "info"},
        )
        assert event.ticker == "SBER"
        assert event.figi == "BBG004730N88"
        assert event.sentiment == "bullish"
        assert event.confidence == 0.85
        assert event.price == 250.50
        assert event.quantity == 10
        assert event.pnl == 120.0
        assert event.reason == "strong signal"
        assert event.details == {"extra": "info"}

    def test_to_dict_omits_none_and_empty(self):
        event = LogEvent(
            timestamp="2026-04-10T12:00:00",
            event_type="error",
            reason="timeout",
        )
        d = event.to_dict()
        assert "timestamp" in d
        assert "event_type" in d
        assert "reason" in d
        # None fields and empty dict should be absent
        assert "ticker" not in d
        assert "details" not in d

    def test_to_dict_includes_populated_fields(self):
        event = LogEvent(
            timestamp="2026-04-10T12:00:00",
            event_type="position_opened",
            ticker="GAZP",
            price=150.0,
            details={"stop_loss": 140.0},
        )
        d = event.to_dict()
        assert d["ticker"] == "GAZP"
        assert d["price"] == 150.0
        assert d["details"] == {"stop_loss": 140.0}


# ===========================================================================
# EventLogger — initialization
# ===========================================================================


class TestEventLoggerInit:
    """Directory creation, log file paths, and log rotation."""

    def test_creates_log_directory(self, tmp_path):
        log_dir = tmp_path / "new_logs"
        assert not log_dir.exists()
        EventLogger(log_dir=log_dir, console_output=False, file_output=True)
        assert log_dir.is_dir()

    def test_does_not_create_dir_when_file_output_false(self, tmp_path):
        log_dir = tmp_path / "should_not_exist"
        EventLogger(log_dir=log_dir, console_output=False, file_output=False)
        assert not log_dir.exists()

    def test_get_log_files_returns_today_paths(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=True)
        jsonl, text = logger._get_log_files()
        today = datetime.now().strftime("%Y-%m-%d")
        assert jsonl == tmp_path / f"events_{today}.jsonl"
        assert text == tmp_path / f"events_{today}.log"

    def test_rotate_deletes_old_files(self, tmp_path):
        """Files with mtime older than max_log_age_days are removed."""
        old_jsonl = tmp_path / "events_2020-01-01.jsonl"
        old_log = tmp_path / "events_2020-01-01.log"
        old_jsonl.write_text("old data")
        old_log.write_text("old data")

        # Set mtime to 60 days ago
        old_mtime = time.time() - 60 * 86400
        os.utime(old_jsonl, (old_mtime, old_mtime))
        os.utime(old_log, (old_mtime, old_mtime))

        # Creating the logger triggers rotation
        EventLogger(log_dir=tmp_path, console_output=False, file_output=True)

        assert not old_jsonl.exists()
        assert not old_log.exists()

    def test_rotate_keeps_recent_files(self, tmp_path):
        """Files newer than max_log_age_days are preserved."""
        recent_jsonl = tmp_path / "events_recent.jsonl"
        recent_log = tmp_path / "events_recent.log"
        recent_jsonl.write_text("recent data")
        recent_log.write_text("recent data")

        # mtime = now (default) — well within the 30-day window
        EventLogger(log_dir=tmp_path, console_output=False, file_output=True)

        assert recent_jsonl.exists()
        assert recent_log.exists()


# ===========================================================================
# EventLogger.log()
# ===========================================================================


class TestEventLoggerLog:
    """Core logging behaviour — file writes and event counting."""

    def test_log_writes_jsonl_file(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=True)
        logger.log(EventType.NEWS_PARSED, ticker="SBER", sentiment="bullish", confidence=0.9)

        jsonl, _ = logger._get_log_files()
        assert jsonl.exists()
        lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["event_type"] == "news_parsed"
        assert data["ticker"] == "SBER"
        assert data["sentiment"] == "bullish"
        assert data["confidence"] == 0.9

    def test_log_writes_text_file(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=True)
        logger.log(EventType.ORDER_PLACED, ticker="GAZP", price=150.0)

        _, text_file = logger._get_log_files()
        assert text_file.exists()
        content = text_file.read_text(encoding="utf-8")
        assert "order_placed" in content
        assert "GAZP" in content
        assert "150.00" in content

    def test_log_updates_event_counts(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=True)
        logger.log(EventType.NEWS_PARSED, ticker="SBER")
        logger.log(EventType.NEWS_PARSED, ticker="GAZP")
        logger.log(EventType.ERROR, reason="oops")

        stats = logger.get_stats()
        assert stats["event_counts"]["news_parsed"] == 2
        assert stats["event_counts"]["error"] == 1
        assert stats["total_events"] == 3

    def test_log_no_file_when_file_output_false(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        logger.log(EventType.NEWS_PARSED, ticker="SBER")

        # No log files should have been created
        log_files = list(tmp_path.glob("events_*"))
        assert len(log_files) == 0

    def test_log_writes_file_when_console_output_false(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=True)
        logger.log(EventType.POSITION_OPENED, ticker="SBER", price=100.0, quantity=5)

        jsonl, text = logger._get_log_files()
        assert jsonl.exists()
        assert text.exists()

    def test_log_multiple_events_appends(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=True)
        logger.log(EventType.NEWS_PARSED, ticker="SBER")
        logger.log(EventType.ORDER_PLACED, ticker="GAZP")
        logger.log(EventType.POSITION_CLOSED, ticker="VTBR", pnl=-10.0)

        jsonl, _ = logger._get_log_files()
        lines = jsonl.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3


# ===========================================================================
# EventLogger.subscribe()
# ===========================================================================


class TestSubscribe:
    """Subscriber notification on log events."""

    def test_subscriber_receives_event(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        received = []
        logger.subscribe(received.append)

        logger.log(EventType.NEWS_PARSED, ticker="SBER")

        assert len(received) == 1
        assert received[0]["event_type"] == "news_parsed"
        assert received[0]["ticker"] == "SBER"

    def test_multiple_subscribers(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        received_a = []
        received_b = []
        logger.subscribe(received_a.append)
        logger.subscribe(received_b.append)

        logger.log(EventType.ERROR, reason="test error")

        assert len(received_a) == 1
        assert len(received_b) == 1
        assert received_a[0]["reason"] == "test error"
        assert received_b[0]["reason"] == "test error"

    def test_subscriber_error_does_not_crash_logger(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=True)
        healthy_received = []

        def bad_subscriber(event):
            raise RuntimeError("subscriber blew up")

        logger.subscribe(bad_subscriber)
        logger.subscribe(healthy_received.append)

        # Should not raise
        logger.log(EventType.BOT_STARTED)

        # Healthy subscriber still received the event
        assert len(healthy_received) == 1
        # Event count still updated
        assert logger.get_stats()["total_events"] == 1

    def test_unsubscribe(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        received = []
        # Store the bound method so the same object is used for subscribe/unsubscribe
        # (unsubscribe uses ``is not`` identity comparison).
        cb = received.append
        logger.subscribe(cb)

        logger.log(EventType.NEWS_PARSED, ticker="SBER")
        assert len(received) == 1

        logger.unsubscribe(cb)
        logger.log(EventType.NEWS_PARSED, ticker="GAZP")
        # Should not receive the second event
        assert len(received) == 1


# ===========================================================================
# EventLogger.get_stats()
# ===========================================================================


class TestGetStats:
    """Statistics tracking."""

    def test_returns_counts_by_event_type(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        logger.log(EventType.NEWS_PARSED, ticker="SBER")
        logger.log(EventType.NEWS_PARSED, ticker="GAZP")
        logger.log(EventType.ORDER_PLACED, ticker="SBER")

        stats = logger.get_stats()
        assert stats["event_counts"]["news_parsed"] == 2
        assert stats["event_counts"]["order_placed"] == 1

    def test_zero_counts_for_unused_types(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        logger.log(EventType.ERROR, reason="test")

        stats = logger.get_stats()
        assert "news_parsed" not in stats["event_counts"]
        assert stats["event_counts"].get("news_parsed", 0) == 0

    def test_total_events(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        for _ in range(5):
            logger.log(EventType.API_RETRY)
        assert logger.get_stats()["total_events"] == 5

    def test_stats_returns_copy(self, tmp_path):
        """Mutating the returned dict must not affect internal state."""
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        logger.log(EventType.ERROR, reason="test")

        stats = logger.get_stats()
        stats["event_counts"]["error"] = 999

        assert logger.get_stats()["event_counts"]["error"] == 1


# ===========================================================================
# _format_console()
# ===========================================================================


class TestFormatConsole:
    """Console formatting produces a readable string."""

    def test_contains_event_type(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        event = LogEvent(
            timestamp="2026-04-10T14:30:00.000",
            event_type="news_parsed",
            ticker="SBER",
        )
        result = logger._format_console(event)
        assert "news_parsed" in result

    def test_contains_ticker(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        event = LogEvent(
            timestamp="2026-04-10T14:30:00.000",
            event_type="order_placed",
            ticker="GAZP",
        )
        result = logger._format_console(event)
        assert "GAZP" in result

    def test_contains_time(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        event = LogEvent(
            timestamp="2026-04-10T14:30:45.123",
            event_type="error",
            reason="timeout",
        )
        result = logger._format_console(event)
        assert "14:30:45" in result

    def test_contains_sentiment_and_confidence(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        event = LogEvent(
            timestamp="2026-04-10T10:00:00.000",
            event_type="signal_validated",
            ticker="SBER",
            sentiment="bullish",
            confidence=0.85,
        )
        result = logger._format_console(event)
        assert "bullish" in result
        assert "85%" in result

    def test_contains_price(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        event = LogEvent(
            timestamp="2026-04-10T10:00:00.000",
            event_type="position_opened",
            price=250.50,
        )
        result = logger._format_console(event)
        assert "@250.50" in result

    def test_contains_pnl(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        event = LogEvent(
            timestamp="2026-04-10T10:00:00.000",
            event_type="position_closed",
            pnl=-15.30,
        )
        result = logger._format_console(event)
        assert "PnL:-15.30" in result

    def test_positive_pnl_has_plus(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        event = LogEvent(
            timestamp="2026-04-10T10:00:00.000",
            event_type="position_closed",
            pnl=42.0,
        )
        result = logger._format_console(event)
        assert "PnL:+42.00" in result

    def test_contains_reason(self, tmp_path):
        logger = EventLogger(log_dir=tmp_path, console_output=False, file_output=False)
        event = LogEvent(
            timestamp="2026-04-10T10:00:00.000",
            event_type="signal_rejected",
            reason="low volume",
        )
        result = logger._format_console(event)
        assert "(low volume)" in result
