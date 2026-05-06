"""Tests for bot.config – TradingConfig, ValidationConfig, Config."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bot.config import Config, TradingConfig, ValidationConfig


# ══════════════════════════════════════════════════════════════════
# TradingConfig defaults
# ══════════════════════════════════════════════════════════════════


class TestTradingConfig:
    def test_default_max_position_rub(self):
        tc = TradingConfig()
        assert tc.max_position_rub == 50_000.0

    def test_default_stop_loss_atr_mult(self):
        tc = TradingConfig()
        assert tc.stop_loss_atr_mult == 2.0

    def test_default_take_profit_atr_mult(self):
        tc = TradingConfig()
        assert tc.take_profit_atr_mult == 3.0

    def test_default_max_position_time_min(self):
        tc = TradingConfig()
        assert tc.max_position_time_min == 15

    def test_default_trailing_stop_pct(self):
        tc = TradingConfig()
        assert tc.trailing_stop_pct == 1.5

    def test_default_dedup_window_sec(self):
        tc = TradingConfig()
        assert tc.dedup_window_sec == 60.0


# ══════════════════════════════════════════════════════════════════
# ValidationConfig defaults
# ══════════════════════════════════════════════════════════════════


class TestValidationConfig:
    def test_default_volume_spike_threshold(self):
        vc = ValidationConfig()
        assert vc.volume_spike_threshold == 3.0

    def test_default_imbalance_threshold(self):
        vc = ValidationConfig()
        assert vc.imbalance_threshold == 0.3

    def test_default_price_move_threshold(self):
        vc = ValidationConfig()
        assert vc.price_move_threshold == 0.1

    def test_default_reaction_window_sec(self):
        vc = ValidationConfig()
        assert vc.reaction_window_sec == 8.0

    def test_default_min_confidence(self):
        vc = ValidationConfig()
        assert vc.min_confidence == 0.4


# ══════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════


class TestConfig:
    def test_get_figi_known_ticker(self, mock_config):
        figi = mock_config.get_figi("SBER")
        assert figi == "BBG004730N88"

    def test_get_figi_unknown_ticker(self, mock_config):
        assert mock_config.get_figi("ZZZZ") is None

    def test_get_figi_case_insensitive(self, mock_config):
        assert mock_config.get_figi("sber") == "BBG004730N88"
        assert mock_config.get_figi("Gazp") == "BBG004730RP0"

    def test_is_allowed_known(self, mock_config):
        assert mock_config.is_allowed("SBER") is True

    def test_is_allowed_unknown(self, mock_config):
        assert mock_config.is_allowed("ZZZZ") is False

    def test_is_allowed_case_insensitive(self, mock_config):
        assert mock_config.is_allowed("sber") is True

    def test_from_env_loads_token(self, tmp_path, fixtures_dir):
        # Write a whitelist that from_env can find
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        src = fixtures_dir / "whitelist.json"
        (data_dir / "whitelist.json").write_text(
            src.read_text(encoding="utf-8"), encoding="utf-8"
        )

        env_values = {
            "TOKEN_TINKOFF": "test-sandbox-token",
            "USE_SANDBOX": "true",
        }

        with (
            patch("bot.config.os.getenv", side_effect=lambda k, *a: env_values.get(k, a[0] if a else None)),
            patch("bot.config.load_dotenv"),
            patch.object(Config, "_load_whitelist"),
        ):
            cfg = Config.from_env()

        assert cfg.tinkoff_token == "test-sandbox-token"
        assert cfg.use_sandbox is True

    def test_from_env_missing_token_raises(self):
        with (
            patch("bot.config.os.getenv", return_value=None),
            patch("bot.config.load_dotenv"),
        ):
            with pytest.raises(ValueError, match="TOKEN_TINKOFF"):
                Config.from_env()

    def test_whitelist_loaded_via_mock_config(self, mock_config):
        # The mock_config fixture loads the test whitelist
        assert len(mock_config.whitelist) == 5
        assert "SBER" in mock_config.whitelist
        assert "GAZP" in mock_config.whitelist


# ══════════════════════════════════════════════════════════════════
# TradingConfig validation
# ══════════════════════════════════════════════════════════════════


class TestTradingConfigValidation:
    def test_max_position_rub_zero_raises(self):
        with pytest.raises(ValueError, match="max_position_rub"):
            TradingConfig(max_position_rub=0)

    def test_max_position_rub_negative_raises(self):
        with pytest.raises(ValueError, match="max_position_rub"):
            TradingConfig(max_position_rub=-100)

    def test_stop_loss_atr_mult_zero_raises(self):
        with pytest.raises(ValueError, match="stop_loss_atr_mult"):
            TradingConfig(stop_loss_atr_mult=0)

    def test_take_profit_atr_mult_negative_raises(self):
        with pytest.raises(ValueError, match="take_profit_atr_mult"):
            TradingConfig(take_profit_atr_mult=-1)

    def test_max_position_time_min_zero_raises(self):
        with pytest.raises(ValueError, match="max_position_time_min"):
            TradingConfig(max_position_time_min=0)

    def test_trailing_stop_pct_zero_raises(self):
        with pytest.raises(ValueError, match="trailing_stop_pct"):
            TradingConfig(trailing_stop_pct=0)

    def test_trailing_stop_pct_over_50_raises(self):
        with pytest.raises(ValueError, match="trailing_stop_pct"):
            TradingConfig(trailing_stop_pct=51)

    def test_trailing_stop_pct_50_ok(self):
        tc = TradingConfig(trailing_stop_pct=50)
        assert tc.trailing_stop_pct == 50

    def test_momentum_candles_zero_raises(self):
        with pytest.raises(ValueError, match="momentum_candles"):
            TradingConfig(momentum_candles=0)

    def test_momentum_candles_61_raises(self):
        with pytest.raises(ValueError, match="momentum_candles"):
            TradingConfig(momentum_candles=61)

    def test_momentum_bearish_ratio_negative_raises(self):
        with pytest.raises(ValueError, match="momentum_bearish_ratio"):
            TradingConfig(momentum_bearish_ratio=-0.1)

    def test_momentum_bearish_ratio_over_1_raises(self):
        with pytest.raises(ValueError, match="momentum_bearish_ratio"):
            TradingConfig(momentum_bearish_ratio=1.1)

    def test_momentum_check_interval_too_low_raises(self):
        with pytest.raises(ValueError, match="momentum_check_interval_sec"):
            TradingConfig(momentum_check_interval_sec=0.5)

    def test_dedup_window_zero_raises(self):
        with pytest.raises(ValueError, match="dedup_window_sec"):
            TradingConfig(dedup_window_sec=0)

    def test_warning_high_trailing_stop(self):
        with pytest.warns(UserWarning, match="unusually high"):
            TradingConfig(trailing_stop_pct=15)

    def test_warning_bad_risk_reward(self):
        with pytest.warns(UserWarning, match="reward/risk"):
            TradingConfig(stop_loss_atr_mult=3.0, take_profit_atr_mult=2.0)


# ══════════════════════════════════════════════════════════════════
# ValidationConfig validation
# ══════════════════════════════════════════════════════════════════


class TestValidationConfigValidation:
    def test_volume_spike_threshold_1_raises(self):
        with pytest.raises(ValueError, match="volume_spike_threshold"):
            ValidationConfig(volume_spike_threshold=1.0)

    def test_imbalance_threshold_negative_raises(self):
        with pytest.raises(ValueError, match="imbalance_threshold"):
            ValidationConfig(imbalance_threshold=-0.1)

    def test_imbalance_threshold_over_1_raises(self):
        with pytest.raises(ValueError, match="imbalance_threshold"):
            ValidationConfig(imbalance_threshold=1.5)

    def test_price_move_threshold_negative_raises(self):
        with pytest.raises(ValueError, match="price_move_threshold"):
            ValidationConfig(price_move_threshold=-0.01)

    def test_reaction_window_zero_raises(self):
        with pytest.raises(ValueError, match="reaction_window_sec"):
            ValidationConfig(reaction_window_sec=0)

    def test_min_confidence_over_1_raises(self):
        with pytest.raises(ValueError, match="min_confidence"):
            ValidationConfig(min_confidence=1.5)

    def test_min_confidence_negative_raises(self):
        with pytest.raises(ValueError, match="min_confidence"):
            ValidationConfig(min_confidence=-0.1)
