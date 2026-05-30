"""Regression tests for offline NLP benchmark on data/nlp_eval.json."""

from pathlib import Path

import pytest

from bot.eval.nlp_metrics import run_nlp_eval


CORPUS = Path("data/nlp_eval.json")


@pytest.fixture(scope="module")
def metrics():
    return run_nlp_eval(corpus_path=CORPUS, rules_only=True)


class TestNlpEvalCorpus:
    def test_corpus_size(self, metrics):
        assert metrics.corpus_size >= 35

    def test_sentiment_accuracy_floor(self, metrics):
        assert metrics.sentiment_accuracy >= 0.90

    def test_sentiment_macro_f1_floor(self, metrics):
        assert metrics.sentiment_macro_f1 >= 0.85

    def test_ticker_recall_floor(self, metrics):
        assert metrics.ticker_recall >= 0.95

    def test_false_positive_rate_ceiling(self, metrics):
        assert metrics.false_positive_rate <= 0.05

    def test_all_samples_evaluated(self, metrics):
        assert len(metrics.sample_results) == metrics.corpus_size
        failed = [s for s in metrics.sample_results if not (s.sentiment_ok and s.ticker_ok)]
        assert not failed, "\n".join(
            f"- {s.text[:60]} expected={s.expected_sentiment}/{s.expected_tickers} "
            f"got={s.predicted_sentiment}/{s.predicted_tickers}"
            for s in failed[:5]
        )
