"""Optional Gemini eval — skipped in CI unless GEMINI_API_KEY is valid."""

import os
from pathlib import Path

import pytest

from bot.eval.nlp_metrics import (
    LLM_CORPUS,
    check_gemini_available,
    compare_rules_and_llm,
    run_nlp_eval,
)

pytestmark = pytest.mark.llm


@pytest.fixture(scope="module")
def gemini_ok():
    from bot.eval.nlp_metrics import _load_env
    _load_env()
    ok, msg = check_gemini_available()
    if not ok:
        pytest.skip(msg)
    return True


@pytest.fixture(scope="module")
def rules_on_llm_corpus():
    return run_nlp_eval(corpus_path=LLM_CORPUS, rules_only=True)


@pytest.fixture(scope="module")
def hybrid_on_llm_corpus(gemini_ok):
    return run_nlp_eval(corpus_path=LLM_CORPUS, rules_only=False)


class TestLlmHardCorpusRulesBaseline:
    """Rules-only baseline on hard corpus — always runs when file exists."""

    def test_corpus_loaded(self, rules_on_llm_corpus):
        assert rules_on_llm_corpus.corpus_size >= 10

    def test_rules_struggle_on_hard_set(self, rules_on_llm_corpus):
        """Hard corpus should not score like the easy rules eval."""
        assert rules_on_llm_corpus.sentiment_accuracy < 1.0


@pytest.mark.llm
class TestGeminiEval:
    def test_gemini_improves_or_matches_rules(self, rules_on_llm_corpus, hybrid_on_llm_corpus):
        assert hybrid_on_llm_corpus.llm_used_count >= 1
        assert hybrid_on_llm_corpus.sentiment_accuracy >= rules_on_llm_corpus.sentiment_accuracy

    def test_comparison_report(self, gemini_ok):
        comparison = compare_rules_and_llm()
        assert "rules_only" in comparison
        assert "with_llm" in comparison
