"""Tests for bot.gemini_config."""

import os
from unittest.mock import patch

from bot.gemini_config import (
    FREE_TIER_MODEL_CANDIDATES,
    classify_gemini_error,
    get_gemini_model_candidates,
)


class TestGeminiConfig:
    def test_default_candidates_are_free_tier_flash_only(self):
        assert FREE_TIER_MODEL_CANDIDATES[0] == "gemini-2.5-flash-lite"
        assert "gemini-3-flash-preview" in FREE_TIER_MODEL_CANDIDATES
        assert not any("2.0" in m for m in FREE_TIER_MODEL_CANDIDATES)

    def test_explicit_model(self):
        assert get_gemini_model_candidates("gemini-3-flash-preview") == [
            "gemini-3-flash-preview"
        ]

    def test_env_model(self, monkeypatch):
        monkeypatch.setenv("GEMINI_MODEL", "gemini-2.5-flash")
        assert get_gemini_model_candidates() == ["gemini-2.5-flash"]

    def test_classify_quota_error(self):
        msg = classify_gemini_error(
            Exception("429 RESOURCE_EXHAUSTED limit: 0"), "gemini-2.5-flash-lite"
        )
        assert "limit=0" in msg or "not your usage" in msg

    def test_classify_geo_error(self):
        msg = classify_gemini_error(
            Exception("User location is not supported"), "gemini-2.5-flash"
        )
        assert "region" in msg
