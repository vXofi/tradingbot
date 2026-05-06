"""Tests for bot.signal_arbiter — deduplication and contradiction detection."""

import threading
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from bot.models import PositionState, Sentiment
from bot.signal_arbiter import ArbiterAction, ArbiterDecision, SignalArbiter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fixed_now(dt: datetime):
    """Return a patcher that pins ``datetime.now()`` inside signal_arbiter."""
    mock_dt = type("MockDatetime", (datetime,), {"now": classmethod(lambda cls, tz=None: dt)})
    return patch("bot.signal_arbiter.datetime", mock_dt)


# ===========================================================================
# PROCEED cases
# ===========================================================================


class TestProceed:
    """Fresh / non-duplicate signals that should pass through."""

    def test_fresh_signal_empty_state(self, make_news_event):
        arbiter = SignalArbiter()
        event = make_news_event()
        action = arbiter.evaluate(event, {})
        assert action.decision == ArbiterDecision.PROCEED
        assert action.existing_position is None

    def test_different_ticker(self, make_news_event):
        arbiter = SignalArbiter()
        arbiter.evaluate(make_news_event(ticker="SBER", figi="figi-sber"), {})
        action = arbiter.evaluate(
            make_news_event(ticker="GAZP", figi="figi-gazp"), {}
        )
        assert action.decision == ArbiterDecision.PROCEED

    def test_different_sentiment_same_ticker(self, make_news_event):
        arbiter = SignalArbiter()
        arbiter.evaluate(
            make_news_event(ticker="SBER", sentiment=Sentiment.BULLISH), {}
        )
        action = arbiter.evaluate(
            make_news_event(ticker="SBER", sentiment=Sentiment.BEARISH), {}
        )
        assert action.decision == ArbiterDecision.PROCEED

    def test_signal_after_window_expires(self, make_news_event):
        """Mock time so the second call happens after the dedup window."""
        t0 = datetime(2025, 1, 1, 12, 0, 0)
        t1 = t0 + timedelta(seconds=61)  # past the default 60s window

        arbiter = SignalArbiter(dedup_window_sec=60.0)

        with _fixed_now(t0):
            arbiter.evaluate(make_news_event(), {})

        with _fixed_now(t1):
            action = arbiter.evaluate(make_news_event(), {})

        assert action.decision == ArbiterDecision.PROCEED

    def test_neutral_event_against_active_position(self, make_news_event, make_position):
        """NEUTRAL sentiment should never trigger contradiction."""
        arbiter = SignalArbiter()
        pos = make_position(direction=Sentiment.BULLISH, state=PositionState.ACTIVE)
        event = make_news_event(sentiment=Sentiment.NEUTRAL)
        action = arbiter.evaluate(event, {pos.figi: pos})
        assert action.decision == ArbiterDecision.PROCEED
        assert action.existing_position is None

    def test_empty_positions_dict(self, make_news_event):
        arbiter = SignalArbiter()
        action = arbiter.evaluate(make_news_event(), {})
        assert action.decision == ArbiterDecision.PROCEED

    def test_position_figi_mismatch(self, make_news_event, make_position):
        """Position exists but for a different FIGI -- no contradiction."""
        arbiter = SignalArbiter()
        pos = make_position(figi="other-figi", direction=Sentiment.BULLISH)
        event = make_news_event(
            figi="BBG004730N88", sentiment=Sentiment.BEARISH
        )
        action = arbiter.evaluate(event, {pos.figi: pos})
        assert action.decision == ArbiterDecision.PROCEED


# ===========================================================================
# DUPLICATE cases
# ===========================================================================


class TestDuplicate:
    """Repeated ticker + sentiment within the dedup window."""

    def test_same_ticker_and_sentiment_within_window(self, make_news_event):
        arbiter = SignalArbiter()
        arbiter.evaluate(make_news_event(), {})
        action = arbiter.evaluate(make_news_event(), {})
        assert action.decision == ArbiterDecision.DUPLICATE

    def test_duplicate_bearish(self, make_news_event):
        arbiter = SignalArbiter()
        arbiter.evaluate(make_news_event(sentiment=Sentiment.BEARISH), {})
        action = arbiter.evaluate(make_news_event(sentiment=Sentiment.BEARISH), {})
        assert action.decision == ArbiterDecision.DUPLICATE

    def test_near_boundary_still_within_window(self, make_news_event):
        """Signal at t=59s (just inside 60s window) is still duplicate."""
        t0 = datetime(2025, 1, 1, 12, 0, 0)
        t1 = t0 + timedelta(seconds=59)

        arbiter = SignalArbiter(dedup_window_sec=60.0)

        with _fixed_now(t0):
            arbiter.evaluate(make_news_event(), {})

        with _fixed_now(t1):
            action = arbiter.evaluate(make_news_event(), {})

        assert action.decision == ArbiterDecision.DUPLICATE

    def test_near_boundary_exactly_at_window(self, make_news_event):
        """Signal at exactly t=60s: the prune uses strict '<', so the old
        signal's timestamp equals the cutoff and is NOT pruned -- still dup."""
        t0 = datetime(2025, 1, 1, 12, 0, 0)
        t1 = t0 + timedelta(seconds=60)

        arbiter = SignalArbiter(dedup_window_sec=60.0)

        with _fixed_now(t0):
            arbiter.evaluate(make_news_event(), {})

        with _fixed_now(t1):
            action = arbiter.evaluate(make_news_event(), {})

        # cutoff = t1 - 60s = t0; condition is t0 < t0 => False => not pruned
        assert action.decision == ArbiterDecision.DUPLICATE

    def test_same_direction_event_with_active_position_is_duplicate(
        self, make_news_event, make_position
    ):
        """A bullish event when we already have a bullish position -- the prior
        evaluate already recorded the signal, so the second identical one is dup."""
        arbiter = SignalArbiter()
        pos = make_position(direction=Sentiment.BULLISH)
        event = make_news_event(sentiment=Sentiment.BULLISH)

        # First call: same direction as position -> PROCEED (not contradiction)
        first = arbiter.evaluate(event, {pos.figi: pos})
        assert first.decision == ArbiterDecision.PROCEED

        # Second identical call: now it is duplicate
        second = arbiter.evaluate(event, {pos.figi: pos})
        assert second.decision == ArbiterDecision.DUPLICATE

    def test_custom_dedup_window(self, make_news_event):
        """With a very short window, signals quickly stop being duplicates."""
        t0 = datetime(2025, 6, 1, 10, 0, 0)
        t1 = t0 + timedelta(seconds=6)

        arbiter = SignalArbiter(dedup_window_sec=5.0)

        with _fixed_now(t0):
            arbiter.evaluate(make_news_event(), {})

        with _fixed_now(t1):
            action = arbiter.evaluate(make_news_event(), {})

        assert action.decision == ArbiterDecision.PROCEED


# ===========================================================================
# CONTRADICTION cases
# ===========================================================================


class TestContradiction:
    """Opposing signal vs an active position."""

    def test_bullish_event_vs_bearish_position(self, make_news_event, make_position):
        arbiter = SignalArbiter()
        pos = make_position(direction=Sentiment.BEARISH, state=PositionState.ACTIVE)
        event = make_news_event(sentiment=Sentiment.BULLISH)
        action = arbiter.evaluate(event, {pos.figi: pos})

        assert action.decision == ArbiterDecision.CONTRADICTION
        assert action.existing_position is pos

    def test_bearish_event_vs_bullish_position(self, make_news_event, make_position):
        arbiter = SignalArbiter()
        pos = make_position(direction=Sentiment.BULLISH, state=PositionState.ACTIVE)
        event = make_news_event(sentiment=Sentiment.BEARISH)
        action = arbiter.evaluate(event, {pos.figi: pos})

        assert action.decision == ArbiterDecision.CONTRADICTION
        assert action.existing_position is pos

    def test_existing_position_set_on_contradiction(self, make_news_event, make_position):
        arbiter = SignalArbiter()
        pos = make_position(direction=Sentiment.BULLISH, state=PositionState.ACTIVE)
        event = make_news_event(sentiment=Sentiment.BEARISH)
        action = arbiter.evaluate(event, {pos.figi: pos})

        assert action.existing_position is not None
        assert action.existing_position.ticker == pos.ticker
        assert action.existing_position.direction == Sentiment.BULLISH

    def test_contradiction_records_signal_so_next_is_duplicate(
        self, make_news_event, make_position
    ):
        """After a contradiction, the same signal within the window is DUPLICATE."""
        arbiter = SignalArbiter()
        pos = make_position(direction=Sentiment.BULLISH, state=PositionState.ACTIVE)
        event = make_news_event(sentiment=Sentiment.BEARISH)

        first = arbiter.evaluate(event, {pos.figi: pos})
        assert first.decision == ArbiterDecision.CONTRADICTION

        # Second identical signal -> DUPLICATE (checked before position logic)
        second = arbiter.evaluate(event, {pos.figi: pos})
        assert second.decision == ArbiterDecision.DUPLICATE

    def test_contradiction_only_for_active_state(
        self, make_news_event, make_position
    ):
        """REVERSING and CLOSED positions must NOT trigger contradiction."""
        arbiter = SignalArbiter()

        for state in (PositionState.REVERSING, PositionState.CLOSED):
            arb = SignalArbiter()
            pos = make_position(direction=Sentiment.BULLISH, state=state)
            event = make_news_event(sentiment=Sentiment.BEARISH)
            action = arb.evaluate(event, {pos.figi: pos})
            assert action.decision == ArbiterDecision.PROCEED, (
                f"Expected PROCEED for state={state}, got {action.decision}"
            )


# ===========================================================================
# Edge cases: REVERSING / CLOSED position
# ===========================================================================


class TestNonActivePositions:
    """Positions that are not ACTIVE should be transparent to the arbiter."""

    def test_reversing_position_ignored(self, make_news_event, make_position):
        arbiter = SignalArbiter()
        pos = make_position(
            direction=Sentiment.BULLISH, state=PositionState.REVERSING
        )
        event = make_news_event(sentiment=Sentiment.BEARISH)
        action = arbiter.evaluate(event, {pos.figi: pos})
        assert action.decision == ArbiterDecision.PROCEED

    def test_closed_position_ignored(self, make_news_event, make_position):
        arbiter = SignalArbiter()
        pos = make_position(
            direction=Sentiment.BULLISH, state=PositionState.CLOSED
        )
        event = make_news_event(sentiment=Sentiment.BEARISH)
        action = arbiter.evaluate(event, {pos.figi: pos})
        assert action.decision == ArbiterDecision.PROCEED


# ===========================================================================
# Thread safety
# ===========================================================================


class TestThreadSafety:
    """Concurrent evaluate calls must not corrupt internal state."""

    def test_concurrent_evaluates(self, make_news_event):
        arbiter = SignalArbiter()
        results: list[ArbiterAction] = []
        errors: list[Exception] = []

        def worker(ticker: str, figi: str):
            try:
                event = make_news_event(ticker=ticker, figi=figi)
                action = arbiter.evaluate(event, {})
                results.append(action)
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=worker, args=(f"T{i}", f"figi-{i}"))
            for i in range(50)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Errors during concurrent execution: {errors}"
        assert len(results) == 50
        # All unique tickers, so every one should be PROCEED
        for action in results:
            assert action.decision == ArbiterDecision.PROCEED

    def test_concurrent_duplicates(self, make_news_event):
        """Many threads sending the exact same signal -- at most one PROCEED."""
        arbiter = SignalArbiter()
        results: list[ArbiterAction] = []
        lock = threading.Lock()

        def worker():
            event = make_news_event(ticker="SBER", sentiment=Sentiment.BULLISH)
            action = arbiter.evaluate(event, {})
            with lock:
                results.append(action)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        proceeds = [r for r in results if r.decision == ArbiterDecision.PROCEED]
        duplicates = [r for r in results if r.decision == ArbiterDecision.DUPLICATE]
        assert len(proceeds) == 1
        assert len(duplicates) == 19


# ===========================================================================
# Custom dedup window
# ===========================================================================


class TestCustomWindow:
    """Verify that the dedup_window_sec constructor argument is respected."""

    def test_large_window_keeps_signals_longer(self, make_news_event):
        t0 = datetime(2025, 1, 1, 12, 0, 0)
        t1 = t0 + timedelta(seconds=90)

        arbiter = SignalArbiter(dedup_window_sec=120.0)

        with _fixed_now(t0):
            arbiter.evaluate(make_news_event(), {})

        # 90s later — still within 120s window
        with _fixed_now(t1):
            action = arbiter.evaluate(make_news_event(), {})
        assert action.decision == ArbiterDecision.DUPLICATE

    def test_zero_window_never_deduplicates(self, make_news_event):
        """With a 0-second window every signal is immediately prunable."""
        t0 = datetime(2025, 3, 1, 8, 0, 0)

        arbiter = SignalArbiter(dedup_window_sec=0.0)

        with _fixed_now(t0):
            arbiter.evaluate(make_news_event(), {})

        # Even at the same instant, cutoff = t0 - 0 = t0; condition t0 < t0 is
        # False so it is NOT pruned — still dup.  Advance by 1 microsecond.
        t1 = t0 + timedelta(microseconds=1)
        with _fixed_now(t1):
            action = arbiter.evaluate(make_news_event(), {})
        assert action.decision == ArbiterDecision.PROCEED


# ===========================================================================
# ArbiterAction defaults
# ===========================================================================


class TestArbiterAction:
    """Verify dataclass defaults on ArbiterAction."""

    def test_default_existing_position_is_none(self):
        action = ArbiterAction(decision=ArbiterDecision.PROCEED)
        assert action.existing_position is None

    def test_proceed_never_has_position(self, make_news_event):
        arbiter = SignalArbiter()
        action = arbiter.evaluate(make_news_event(), {})
        assert action.existing_position is None

    def test_duplicate_never_has_position(self, make_news_event):
        arbiter = SignalArbiter()
        arbiter.evaluate(make_news_event(), {})
        action = arbiter.evaluate(make_news_event(), {})
        assert action.decision == ArbiterDecision.DUPLICATE
        assert action.existing_position is None
