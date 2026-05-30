"""
Offline NLP benchmark for NewsParser.

Supports rules-only regression (CI) and optional Gemini comparison
on data/nlp_eval_llm.json (requires valid GEMINI_API_KEY).
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from bot.listeners.news_parser import NewsParser
from bot.models import Sentiment
from bot.gemini_config import probe_gemini


DEFAULT_CORPUS = Path("data/nlp_eval.json")
LLM_CORPUS = Path("data/nlp_eval_llm.json")


@dataclass
class SampleResult:
    text: str
    expected_sentiment: str
    expected_tickers: list[str]
    predicted_success: bool
    predicted_sentiment: str
    predicted_tickers: list[str]
    sentiment_ok: bool
    ticker_ok: bool
    category: str = ""
    method: str = ""


@dataclass
class NlpEvalMetrics:
    corpus_size: int
    rules_only: bool
    sentiment_accuracy: float
    sentiment_macro_f1: float
    ticker_recall: float
    ticker_precision: float
    false_positive_rate: float
    actionable_count: int
    ticker_labeled_count: int
    negative_count: int
    llm_used_count: int = 0
    sample_results: list[SampleResult] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "corpus_size": self.corpus_size,
            "rules_only": self.rules_only,
            "sentiment_accuracy": round(self.sentiment_accuracy, 3),
            "sentiment_macro_f1": round(self.sentiment_macro_f1, 3),
            "ticker_recall": round(self.ticker_recall, 3),
            "ticker_precision": round(self.ticker_precision, 3),
            "false_positive_rate": round(self.false_positive_rate, 3),
            "actionable_count": self.actionable_count,
            "ticker_labeled_count": self.ticker_labeled_count,
            "negative_count": self.negative_count,
            "llm_used_count": self.llm_used_count,
        }


def _f1(precision: float, recall: float) -> float:
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _macro_f1(y_true: list[str], y_pred: list[str], labels: list[str]) -> float:
    scores: list[float] = []
    for label in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != label and p == label)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == label and p != label)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        scores.append(_f1(prec, rec))
    return sum(scores) / len(labels) if scores else 0.0


def _match_sentiment(expected: str, success: bool, sentiment: Sentiment) -> bool:
    if expected == "none":
        return not success or sentiment == Sentiment.NEUTRAL
    return success and sentiment.value == expected


def _match_tickers(expected: list[str], predicted: list[str]) -> bool:
    if not expected:
        return True
    if not predicted:
        return False
    return any(t in predicted for t in expected)


def _ticker_precision(expected: list[str], predicted: list[str]) -> Optional[tuple[int, int]]:
    if not predicted:
        return None
    if not expected:
        return (0, len(predicted))
    hits = sum(1 for t in predicted if t in expected)
    return hits, len(predicted)


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(".env"))
    except ImportError:
        pass


def check_gemini_available(api_key: Optional[str] = None) -> tuple[bool, str]:
    """Probe Gemini API; tries free-tier model candidates in order."""
    _load_env()
    key = (api_key or os.getenv("GEMINI_API_KEY") or "").strip()
    ok, message, _model = probe_gemini(key)
    return ok, message


def run_nlp_eval(
    corpus_path: Path = DEFAULT_CORPUS,
    data_dir: Path = Path("data"),
    rules_only: bool = True,
) -> NlpEvalMetrics:
    with open(corpus_path, encoding="utf-8") as f:
        payload = json.load(f)

    parser = NewsParser(
        data_dir=data_dir,
        use_llm_fallback=not rules_only,
    )

    sample_results: list[SampleResult] = []
    sentiment_true: list[str] = []
    sentiment_pred: list[str] = []

    ticker_hits = 0
    ticker_expected = 0
    tp_sum = 0
    pred_sum = 0

    negatives = 0
    false_positives = 0
    llm_used = 0

    for item in payload["samples"]:
        text = item["text"]
        expected_sentiment = item["expected_sentiment"]
        expected_tickers = item.get("expected_tickers") or []
        category = item.get("category", "")

        result = parser.parse(text)
        pred_sentiment = result.sentiment.value
        pred_tickers = list(result.tickers)
        method = result.method.value

        if method in ("semantic", "hybrid"):
            llm_used += 1

        sentiment_ok = _match_sentiment(expected_sentiment, result.success, result.sentiment)
        ticker_ok = _match_tickers(expected_tickers, pred_tickers)

        sample_results.append(SampleResult(
            text=text,
            expected_sentiment=expected_sentiment,
            expected_tickers=expected_tickers,
            predicted_success=result.success,
            predicted_sentiment=pred_sentiment,
            predicted_tickers=pred_tickers,
            sentiment_ok=sentiment_ok,
            ticker_ok=ticker_ok,
            category=category,
            method=method,
        ))

        if expected_sentiment in ("bullish", "bearish"):
            sentiment_true.append(expected_sentiment)
            sentiment_pred.append(pred_sentiment if result.success else "neutral")

        if expected_tickers:
            ticker_expected += 1
            if ticker_ok:
                ticker_hits += 1
            prec = _ticker_precision(expected_tickers, pred_tickers)
            if prec:
                tp_sum += prec[0]
                pred_sum += prec[1]

        if expected_sentiment == "none":
            negatives += 1
            if result.success and result.sentiment != Sentiment.NEUTRAL:
                false_positives += 1

    actionable = len(sentiment_true)
    sentiment_accuracy = (
        sum(1 for t, p in zip(sentiment_true, sentiment_pred) if t == p) / actionable
        if actionable else 0.0
    )
    macro_f1 = _macro_f1(sentiment_true, sentiment_pred, ["bullish", "bearish"])
    ticker_recall = ticker_hits / ticker_expected if ticker_expected else 0.0
    ticker_precision = tp_sum / pred_sum if pred_sum else 0.0
    fpr = false_positives / negatives if negatives else 0.0

    return NlpEvalMetrics(
        corpus_size=len(sample_results),
        rules_only=rules_only,
        sentiment_accuracy=sentiment_accuracy,
        sentiment_macro_f1=macro_f1,
        ticker_recall=ticker_recall,
        ticker_precision=ticker_precision,
        false_positive_rate=fpr,
        actionable_count=actionable,
        ticker_labeled_count=ticker_expected,
        negative_count=negatives,
        llm_used_count=llm_used,
        sample_results=sample_results,
    )


def compare_rules_and_llm(
    corpus_path: Path = LLM_CORPUS,
    data_dir: Path = Path("data"),
) -> dict:
    rules = run_nlp_eval(corpus_path=corpus_path, data_dir=data_dir, rules_only=True)
    hybrid = run_nlp_eval(corpus_path=corpus_path, data_dir=data_dir, rules_only=False)
    return {"rules_only": rules.to_dict(), "with_llm": hybrid.to_dict()}


def format_metrics_report(metrics: NlpEvalMetrics) -> str:
    mode = "rules-only" if metrics.rules_only else "rules + Gemini fallback"
    d = metrics.to_dict()
    lines = [
        f"NLP benchmark ({mode})",
        f"  corpus: {d['corpus_size']} headlines",
        f"  sentiment accuracy: {d['sentiment_accuracy']:.1%}  macro-F1: {d['sentiment_macro_f1']:.3f}",
        f"  ticker recall: {d['ticker_recall']:.1%}  precision: {d['ticker_precision']:.1%}",
        f"  false positive rate (negative set): {d['false_positive_rate']:.1%}",
    ]
    if not metrics.rules_only:
        lines.append(f"  LLM-used samples: {d['llm_used_count']}")
    return "\n".join(lines)


def format_comparison_report(comparison: dict) -> str:
    r = comparison["rules_only"]
    h = comparison["with_llm"]
    lines = [
        "NLP comparison on LLM-hard corpus",
        f"  rules-only:  accuracy {r['sentiment_accuracy']:.1%}  recall {r['ticker_recall']:.1%}  FPR {r['false_positive_rate']:.1%}",
        f"  with Gemini: accuracy {h['sentiment_accuracy']:.1%}  recall {h['ticker_recall']:.1%}  FPR {h['false_positive_rate']:.1%}  (LLM calls: {h['llm_used_count']})",
    ]
    return "\n".join(lines)


def main():
    _load_env()
    parser = argparse.ArgumentParser(description="Run NLP eval benchmark")
    parser.add_argument(
        "--corpus",
        type=Path,
        default=DEFAULT_CORPUS,
        help="Path to eval JSON corpus",
    )
    parser.add_argument(
        "--with-llm",
        action="store_true",
        help="Enable Gemini fallback (requires valid GEMINI_API_KEY)",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Compare rules-only vs with-llm on corpus (default: nlp_eval_llm.json)",
    )
    parser.add_argument(
        "--check-gemini",
        action="store_true",
        help="Only verify Gemini API key and exit",
    )
    args = parser.parse_args()

    if args.check_gemini:
        ok, msg = check_gemini_available()
        print(msg)
        raise SystemExit(0 if ok else 1)

    if args.compare:
        corpus = args.corpus if args.corpus != DEFAULT_CORPUS else LLM_CORPUS
        ok, msg = check_gemini_available()
        if not ok:
            print(f"Cannot compare with LLM: {msg}")
            raise SystemExit(1)
        comparison = compare_rules_and_llm(corpus_path=corpus)
        print(format_comparison_report(comparison))
        return

    if args.with_llm:
        ok, msg = check_gemini_available()
        if not ok:
            print(f"Cannot run with LLM: {msg}")
            raise SystemExit(1)

    metrics = run_nlp_eval(
        corpus_path=args.corpus,
        rules_only=not args.with_llm,
    )
    print(format_metrics_report(metrics))


if __name__ == "__main__":
    main()
