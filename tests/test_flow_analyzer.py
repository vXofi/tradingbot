"""Tests for FlowAnalyzer pure methods."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from bot.models import FlowAnalysis, Sentiment, ValidationResult
from bot.validators.flow_analyzer import FlowAnalyzer


@pytest.fixture()
def analyzer(mock_config):
    """FlowAnalyzer with a mocked client — only pure methods are tested."""
    client = MagicMock()
    return FlowAnalyzer(client, mock_config)


# ── _combine_sentiments ──────────────────────────────────────


class TestCombineSentiments:
    """Tests for FlowAnalyzer._combine_sentiments."""

    def test_all_neutral_returns_neutral(self, analyzer):
        result = analyzer._combine_sentiments(
            Sentiment.NEUTRAL, Sentiment.NEUTRAL, 0.0
        )
        assert result == Sentiment.NEUTRAL

    def test_bullish_orderbook_only(self, analyzer):
        result = analyzer._combine_sentiments(
            Sentiment.BULLISH, Sentiment.NEUTRAL, 0.0
        )
        assert result == Sentiment.BULLISH

    def test_bearish_trades_only(self, analyzer):
        result = analyzer._combine_sentiments(
            Sentiment.NEUTRAL, Sentiment.BEARISH, 0.0
        )
        assert result == Sentiment.BEARISH

    def test_two_bullish_sources(self, analyzer):
        result = analyzer._combine_sentiments(
            Sentiment.BULLISH, Sentiment.BULLISH, 0.0
        )
        assert result == Sentiment.BULLISH

    def test_conflicting_cancel_to_neutral(self, analyzer):
        result = analyzer._combine_sentiments(
            Sentiment.BULLISH, Sentiment.BEARISH, 0.0
        )
        assert result == Sentiment.NEUTRAL

    def test_three_way_bullish_agreement(self, analyzer):
        # price_change above threshold → BULLISH price sentiment
        result = analyzer._combine_sentiments(
            Sentiment.BULLISH, Sentiment.BULLISH, 0.5
        )
        assert result == Sentiment.BULLISH

    def test_three_way_bearish_agreement(self, analyzer):
        result = analyzer._combine_sentiments(
            Sentiment.BEARISH, Sentiment.BEARISH, -0.5
        )
        assert result == Sentiment.BEARISH

    def test_positive_price_drives_bullish(self, analyzer):
        # price_change > price_move_threshold (0.1) → BULLISH contribution
        result = analyzer._combine_sentiments(
            Sentiment.NEUTRAL, Sentiment.NEUTRAL, 0.2
        )
        assert result == Sentiment.BULLISH

    def test_negative_price_drives_bearish(self, analyzer):
        result = analyzer._combine_sentiments(
            Sentiment.NEUTRAL, Sentiment.NEUTRAL, -0.2
        )
        assert result == Sentiment.BEARISH

    def test_price_below_threshold_stays_neutral(self, analyzer):
        # price_move_threshold is 0.1; price_change within range → no contribution
        result = analyzer._combine_sentiments(
            Sentiment.NEUTRAL, Sentiment.NEUTRAL, 0.05
        )
        assert result == Sentiment.NEUTRAL

    def test_price_at_exact_threshold_stays_neutral(self, analyzer):
        # price_change == threshold → not strictly greater, stays NEUTRAL
        result = analyzer._combine_sentiments(
            Sentiment.NEUTRAL, Sentiment.NEUTRAL, 0.1
        )
        assert result == Sentiment.NEUTRAL

    def test_two_bullish_vs_bearish_price_cancels(self, analyzer):
        # orderbook=BULLISH, trades=BULLISH, price=-0.5 → BEARISH price
        # 2 BULLISH vs 1 BEARISH → still has both → NEUTRAL
        result = analyzer._combine_sentiments(
            Sentiment.BULLISH, Sentiment.BULLISH, -0.5
        )
        assert result == Sentiment.NEUTRAL


# ── _calculate_confidence ────────────────────────────────────


class TestCalculateConfidence:
    """Tests for FlowAnalyzer._calculate_confidence."""

    def test_zero_everything(self, analyzer):
        conf = analyzer._calculate_confidence(
            imbalance=0.0, volume_ratio=0.0, price_change=0.0,
            directional_agree=False,
        )
        assert conf == 0.0

    def test_max_imbalance_contributes_030(self, analyzer):
        conf = analyzer._calculate_confidence(
            imbalance=1.0, volume_ratio=0.0, price_change=0.0,
            directional_agree=False,
        )
        assert conf == pytest.approx(0.3)

    def test_imbalance_scales_with_abs_value(self, analyzer):
        conf_half = analyzer._calculate_confidence(
            imbalance=0.5, volume_ratio=0.0, price_change=0.0,
            directional_agree=False,
        )
        conf_full = analyzer._calculate_confidence(
            imbalance=1.0, volume_ratio=0.0, price_change=0.0,
            directional_agree=False,
        )
        assert conf_half == pytest.approx(0.15)
        assert conf_full == pytest.approx(0.30)
        assert conf_half < conf_full

    def test_negative_imbalance_uses_abs(self, analyzer):
        conf_pos = analyzer._calculate_confidence(
            imbalance=0.7, volume_ratio=0.0, price_change=0.0,
            directional_agree=False,
        )
        conf_neg = analyzer._calculate_confidence(
            imbalance=-0.7, volume_ratio=0.0, price_change=0.0,
            directional_agree=False,
        )
        assert conf_pos == pytest.approx(conf_neg)

    def test_volume_spike_above_threshold(self, analyzer):
        # volume_spike_threshold=3.0; ratio=3.0 triggers contribution
        # normalized = min(3.0 / 6.0, 1.0) = 0.5 → 0.5 * 0.3 = 0.15
        conf = analyzer._calculate_confidence(
            imbalance=0.0, volume_ratio=3.0, price_change=0.0,
            directional_agree=False,
        )
        assert conf == pytest.approx(0.15)

    def test_volume_below_threshold_no_contribution(self, analyzer):
        conf = analyzer._calculate_confidence(
            imbalance=0.0, volume_ratio=2.0, price_change=0.0,
            directional_agree=False,
        )
        assert conf == pytest.approx(0.0)

    def test_price_movement_contributes_up_to_020(self, analyzer):
        # price_factor = min(abs(1.0) / 1.0, 1.0) = 1.0 → 1.0 * 0.2 = 0.2
        conf = analyzer._calculate_confidence(
            imbalance=0.0, volume_ratio=0.0, price_change=1.0,
            directional_agree=False,
        )
        assert conf == pytest.approx(0.2)

    def test_directional_agreement_adds_020(self, analyzer):
        conf = analyzer._calculate_confidence(
            imbalance=0.0, volume_ratio=0.0, price_change=0.0,
            directional_agree=True,
        )
        assert conf == pytest.approx(0.2)

    def test_all_components_sum(self, analyzer):
        # imbalance=1.0 → 0.3
        # volume_ratio=6.0 → normalized=min(6.0/6.0,1.0)=1.0 → 0.3
        # price_change=1.0 → 0.2
        # directional_agree → 0.2
        # total = 1.0
        conf = analyzer._calculate_confidence(
            imbalance=1.0, volume_ratio=6.0, price_change=1.0,
            directional_agree=True,
        )
        assert conf == pytest.approx(1.0)

    def test_confidence_capped_at_1(self, analyzer):
        # Extreme values that would exceed 1.0
        conf = analyzer._calculate_confidence(
            imbalance=2.0, volume_ratio=100.0, price_change=5.0,
            directional_agree=True,
        )
        assert conf == pytest.approx(1.0)


# ── _confirms_signal ─────────────────────────────────────────


def _make_analysis(sentiment, confidence):
    """Helper to build a FlowAnalysis with given sentiment and confidence."""
    return FlowAnalysis(
        figi="BBG004730N88",
        timestamp=datetime.now(),
        imbalance=0.0,
        volume_ratio=0.0,
        price_change_percent=0.0,
        detected_sentiment=sentiment,
        validation_result=ValidationResult.INCONCLUSIVE,
        confidence=confidence,
    )


class TestConfirmsSignal:
    """Tests for FlowAnalyzer._confirms_signal."""

    def test_matching_sentiment_high_confidence(self, analyzer):
        analysis = _make_analysis(Sentiment.BULLISH, confidence=0.8)
        assert analyzer._confirms_signal(analysis, Sentiment.BULLISH) is True

    def test_matching_sentiment_low_confidence(self, analyzer):
        # min_confidence is 0.4; 0.2 is below
        analysis = _make_analysis(Sentiment.BULLISH, confidence=0.2)
        assert analyzer._confirms_signal(analysis, Sentiment.BULLISH) is False

    def test_non_matching_sentiment(self, analyzer):
        analysis = _make_analysis(Sentiment.BEARISH, confidence=0.9)
        assert analyzer._confirms_signal(analysis, Sentiment.BULLISH) is False

    def test_neutral_expected_always_true(self, analyzer):
        analysis = _make_analysis(Sentiment.BEARISH, confidence=0.0)
        assert analyzer._confirms_signal(analysis, Sentiment.NEUTRAL) is True

    def test_exact_boundary_confidence(self, analyzer):
        # confidence == min_confidence (0.4) should confirm
        analysis = _make_analysis(Sentiment.BEARISH, confidence=0.4)
        assert analyzer._confirms_signal(analysis, Sentiment.BEARISH) is True

    def test_just_below_boundary(self, analyzer):
        analysis = _make_analysis(Sentiment.BEARISH, confidence=0.39)
        assert analyzer._confirms_signal(analysis, Sentiment.BEARISH) is False
