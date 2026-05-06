"""Tests for bot.models — dataclasses, enums, and computed properties."""

from datetime import datetime

from bot.models import (
    OrderBookSnapshot,
    Position,
    PositionState,
    Sentiment,
    ValidationResult,
)


# ── Enum values ──────────────────────────────────────────────


class TestSentimentEnum:
    def test_bullish_value(self):
        assert Sentiment.BULLISH.value == "bullish"

    def test_bearish_value(self):
        assert Sentiment.BEARISH.value == "bearish"

    def test_neutral_value(self):
        assert Sentiment.NEUTRAL.value == "neutral"


class TestValidationResultEnum:
    def test_confirmed_value(self):
        assert ValidationResult.CONFIRMED.value == "confirmed"

    def test_rejected_value(self):
        assert ValidationResult.REJECTED.value == "rejected"

    def test_inconclusive_value(self):
        assert ValidationResult.INCONCLUSIVE.value == "inconclusive"


class TestPositionStateEnum:
    def test_active_value(self):
        assert PositionState.ACTIVE.value == "active"

    def test_reversing_value(self):
        assert PositionState.REVERSING.value == "reversing"

    def test_closed_value(self):
        assert PositionState.CLOSED.value == "closed"


# ── OrderBookSnapshot properties ────────────────────────────


def _snapshot(bids=None, asks=None):
    return OrderBookSnapshot(
        figi="BBG004730N88",
        timestamp=datetime.now(),
        bids=bids or [],
        asks=asks or [],
    )


class TestOrderBookImbalance:
    def test_all_bids_returns_1(self):
        snap = _snapshot(bids=[(100.0, 50), (99.0, 30)])
        assert snap.imbalance == 1.0

    def test_all_asks_returns_neg1(self):
        snap = _snapshot(asks=[(101.0, 40), (102.0, 60)])
        assert snap.imbalance == -1.0

    def test_equal_volumes_returns_0(self):
        snap = _snapshot(bids=[(100.0, 50)], asks=[(101.0, 50)])
        assert snap.imbalance == 0.0

    def test_empty_book_returns_0(self):
        snap = _snapshot()
        assert snap.imbalance == 0.0


class TestOrderBookVolumes:
    def test_bid_volume_sums_across_levels(self):
        snap = _snapshot(bids=[(100.0, 10), (99.0, 20), (98.0, 30)])
        assert snap.bid_volume == 60

    def test_ask_volume_sums_across_levels(self):
        snap = _snapshot(asks=[(101.0, 15), (102.0, 25)])
        assert snap.ask_volume == 40

    def test_bid_volume_empty(self):
        snap = _snapshot()
        assert snap.bid_volume == 0

    def test_ask_volume_empty(self):
        snap = _snapshot()
        assert snap.ask_volume == 0


class TestOrderBookBestPrices:
    def test_best_bid_returns_first_entry(self):
        snap = _snapshot(bids=[(100.0, 10), (99.0, 20)])
        assert snap.best_bid == 100.0

    def test_best_bid_none_when_empty(self):
        snap = _snapshot()
        assert snap.best_bid is None

    def test_best_ask_returns_first_entry(self):
        snap = _snapshot(asks=[(101.0, 5), (102.0, 10)])
        assert snap.best_ask == 101.0

    def test_best_ask_none_when_empty(self):
        snap = _snapshot()
        assert snap.best_ask is None


class TestOrderBookSpread:
    def test_spread_ask_minus_bid(self):
        snap = _snapshot(bids=[(100.0, 10)], asks=[(102.0, 10)])
        assert snap.spread == 2.0

    def test_spread_none_when_bids_empty(self):
        snap = _snapshot(asks=[(101.0, 10)])
        assert snap.spread is None

    def test_spread_none_when_asks_empty(self):
        snap = _snapshot(bids=[(100.0, 10)])
        assert snap.spread is None

    def test_spread_none_when_both_empty(self):
        snap = _snapshot()
        assert snap.spread is None


# ── Position.__post_init__ ──────────────────────────────────


class TestPositionPostInit:
    def test_peak_price_defaults_to_entry_price(self, make_position):
        pos = make_position(entry_price=150.0, peak_price=0.0)
        assert pos.peak_price == 150.0

    def test_peak_price_preserved_when_set(self, make_position):
        pos = make_position(entry_price=100.0, peak_price=120.0)
        assert pos.peak_price == 120.0
