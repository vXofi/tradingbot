"""
Hybrid news parser with three-layer signal detection.

Architecture:
  Layer 1: Company financial keywords (dividends, earnings, etc.)
  Layer 2: Corporate event keywords (criminal, regulatory, M&A, debt)
  Layer 3: Geopolitical/macro keywords -> sector routing

Entity resolution:
  1. data/entities.json  (brands/products/subsidiaries -> ticker)
  2. ticker_synonyms     (company names -> ticker)
  3. Regex               (uppercase 4-5 letter codes in whitelist)

LLM fallback:
  Gemini API for entity resolution + sentiment on market-relevant
  news that keyword layers can't handle.
"""

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional

from ..models import NewsEvent, Sentiment
from ..utils.retry import RateLimiter


class AnalysisMethod(Enum):
    KEYWORD = "keyword"
    CORPORATE = "corporate"
    GEOPOLITICAL = "geopolitical"
    SEMANTIC = "semantic"
    HYBRID = "hybrid"
    FAILED = "failed"


@dataclass
class ParseResult:
    success: bool
    tickers: list[str] = field(default_factory=list)
    figis: list[str] = field(default_factory=list)
    sentiment: Sentiment = Sentiment.NEUTRAL
    confidence: float = 0.0
    keywords_found: list[str] = field(default_factory=list)
    method: AnalysisMethod = AnalysisMethod.FAILED
    signal_type: str = "company"
    reasoning: Optional[str] = None
    raw_text: str = ""

    @property
    def ticker(self) -> Optional[str]:
        return self.tickers[0] if self.tickers else None

    @property
    def figi(self) -> Optional[str]:
        return self.figis[0] if self.figis else None


class NewsParser:
    """
    Three-layer hybrid news parser.

    Parse flow:
    1. Filter spam/ads
    2. Resolve entities (brands -> tickers, synonyms, regex)
    3. Layer 1: company financial keywords
    4. Layer 2: corporate events (criminal, regulatory, M&A, debt)
    5. Best of layers 1-2 -> return if confident with ticker
    6. Layer 3: geopolitical/macro -> sector routing
    7. Market relevance check -> LLM fallback
    """

    def __init__(
        self,
        gemini_api_key: Optional[str] = None,
        data_dir: Path = Path("data"),
        min_keyword_confidence: float = 0.7,
        use_llm_fallback: bool = True,
    ):
        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY")
        self.data_dir = data_dir
        self.min_keyword_confidence = min_keyword_confidence
        self.use_llm_fallback = use_llm_fallback

        self._gemini_model = None
        self._rate_limiter = RateLimiter(calls_per_minute=55, calls_per_day=1500)

        self._load_keywords()
        self._load_entities()
        self._load_ticker_mapping()
        self._load_sectors()

    # ── data loading ─────────────────────────────────────────

    def _load_keywords(self):
        keywords_path = self.data_dir / "keywords.json"
        self._kw_data = {}

        if keywords_path.exists():
            with open(keywords_path, "r", encoding="utf-8") as f:
                self._kw_data = json.load(f)

        self.bullish_keywords = self._kw_data.get("bullish", {}).get("keywords", [])
        self.bearish_keywords = self._kw_data.get("bearish", {}).get("keywords", [])
        self.ignore_keywords = self._kw_data.get("ignore", {}).get("keywords", [])
        self.geo_triggers = self._kw_data.get("geopolitical", {})
        self.criminal_keywords = self._kw_data.get("criminal", {}).get("keywords", [])
        self.criminal_confidence = self._kw_data.get("criminal", {}).get("confidence", 0.8)
        self.regulatory_data = self._kw_data.get("regulatory", {})
        self.ma_data = self._kw_data.get("ma", {})
        self.debt_data = self._kw_data.get("debt", {})
        self.market_relevance_keywords = self._kw_data.get("market_relevance", {}).get("keywords", [])

    def _load_entities(self):
        """Load brand/product/subsidiary -> ticker from entities.json."""
        self.entity_map: dict[str, str] = {}
        entities_path = self.data_dir / "entities.json"

        if entities_path.exists():
            with open(entities_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for ticker, aliases in data.items():
                if ticker.startswith("_"):
                    continue
                if isinstance(aliases, list):
                    for alias in aliases:
                        self.entity_map[alias.lower()] = ticker

    def _load_ticker_mapping(self):
        self.ticker_synonyms = {
            "сбер": "SBER", "сбербанк": "SBER", "сбера": "SBER",
            "sber": "SBER", "$sber": "SBER",
            "газпром": "GAZP", "газпрома": "GAZP", "gazp": "GAZP", "$gazp": "GAZP",
            "лукойл": "LKOH", "лукойла": "LKOH", "lkoh": "LKOH",
            "норникель": "GMKN", "норильский никель": "GMKN", "gmkn": "GMKN",
            "яндекс": "YDEX", "yandex": "YDEX", "yndx": "YDEX", "ydex": "YDEX",
            "роснефть": "ROSN", "rosn": "ROSN",
            "татнефть": "TATN", "tatn": "TATN",
            "мтс": "MTSS", "mtss": "MTSS",
            "магнит": "MGNT", "mgnt": "MGNT",
            "алроса": "ALRS", "alrs": "ALRS",
            "северсталь": "CHMF", "chmf": "CHMF",
            "нлмк": "NLMK", "nlmk": "NLMK",
            "полюс": "PLZL", "plzl": "PLZL",
            "мосбиржа": "MOEX", "московская биржа": "MOEX", "moex": "MOEX",
            "втб": "VTBR", "vtbr": "VTBR",
            "фосагро": "PHOR", "phor": "PHOR",
            "русал": "RUAL", "rusal": "RUAL", "rual": "RUAL",
            "афк система": "AFKS", "afks": "AFKS",
            "пик": "PIKK", "pikk": "PIKK",
            "новатэк": "NVTK", "novatek": "NVTK", "nvtk": "NVTK",
            # new stocks
            "самолёт": "SMLT", "самолет": "SMLT", "smlt": "SMLT",
            "озон": "OZON", "ozon": "OZON",
            "vk": "VKCO", "вк": "VKCO", "vkco": "VKCO",
            "headhunter": "HHRU", "хедхантер": "HHRU", "hhru": "HHRU", "hh.ru": "HHRU",
            "positive technologies": "POSI", "позитив": "POSI", "posi": "POSI",
            "т-банк": "TCSG", "тинькофф": "TCSG", "tinkoff": "TCSG", "tcsg": "TCSG",
            "банк спб": "BSPB", "банк санкт-петербург": "BSPB", "bspb": "BSPB",
            "мкб": "CBOM", "cbom": "CBOM", "московский кредитный банк": "CBOM",
            "сургутнефтегаз": "SNGS", "sngs": "SNGS",
            "транснефть": "TRNFP", "trnfp": "TRNFP",
            "интер рао": "IRAO", "интеррао": "IRAO", "irao": "IRAO",
            "русгидро": "HYDR", "hydr": "HYDR",
            "юнипро": "UPRO", "upro": "UPRO",
            "ммк": "MAGN", "magn": "MAGN", "магнитогорский": "MAGN",
            "x5": "FIVE", "пятёрочка": "FIVE", "пятерочка": "FIVE", "five": "FIVE",
            "м.видео": "MVID", "мвидео": "MVID", "mvid": "MVID",
            "совкомфлот": "FLOT", "flot": "FLOT",
            "сегежа": "SGZH", "sgzh": "SGZH", "segezha": "SGZH",
            "ренессанс": "RENI", "reni": "RENI",
        }

        self.ticker_to_figi: dict[str, str] = {}
        whitelist_path = self.data_dir / "whitelist.json"
        if whitelist_path.exists():
            with open(whitelist_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data.get("instruments", []):
                    self.ticker_to_figi[item["ticker"]] = item["figi"]

    def _load_sectors(self):
        self.sectors: dict[str, dict] = {}
        sectors_path = self.data_dir / "sectors.json"
        if sectors_path.exists():
            with open(sectors_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.sectors = data.get("sectors", {})

    # ── entity resolution ────────────────────────────────────

    def _resolve_entities(self, text: str) -> list[str]:
        """
        Multi-layer entity resolution.
        Returns list of resolved tickers (can be multiple).
        Priority: entities.json > ticker_synonyms > regex.
        """
        text_lower = text.lower()
        found: dict[str, int] = {}

        # 1. Entity dictionary (brands/products/subsidiaries)
        for alias, ticker in self.entity_map.items():
            if self._word_match(alias, text_lower):
                found[ticker] = max(found.get(ticker, 0), 3)

        # 2. Ticker synonyms (company names)
        for synonym, ticker in self.ticker_synonyms.items():
            if self._word_match(synonym, text_lower):
                found[ticker] = max(found.get(ticker, 0), 2)

        # 3. Regex: uppercase 4-5 letter tickers in the text
        for match in re.findall(r'\b([A-Z]{4,5})\b', text):
            if match in self.ticker_to_figi:
                found[match] = max(found.get(match, 0), 1)

        return sorted(found, key=lambda t: found[t], reverse=True)

    @staticmethod
    def _word_match(needle: str, haystack: str) -> bool:
        """Substring match with word-boundary check for short needles."""
        pos = haystack.find(needle)
        if pos == -1:
            return False
        if len(needle) >= 4:
            return True
        # Short synonyms (<=3 chars) must be at word boundaries to avoid
        # false positives like "вк" inside "ставку".
        before_ok = pos == 0 or not haystack[pos - 1].isalpha()
        end = pos + len(needle)
        after_ok = end >= len(haystack) or not haystack[end].isalpha()
        return before_ok and after_ok

    # ── negation helpers ──────────────────────────────────────

    _NEGATION_MARKERS = ("не ", "без ", "нет ", "ни ", "отсутств", "не будет")
    _NEGATION_WINDOW = 30  # chars before the keyword match

    def _is_negated(self, text_lower: str, keyword: str) -> bool:
        """Check if *keyword* appears under a Russian negation within a small
        preceding window (e.g. 'не выросла', 'без роста')."""
        kw = keyword.lower()
        start = 0
        while True:
            pos = text_lower.find(kw, start)
            if pos == -1:
                return False
            window_start = max(0, pos - self._NEGATION_WINDOW)
            window = text_lower[window_start:pos]
            if any(neg in window for neg in self._NEGATION_MARKERS):
                return True
            start = pos + len(kw)

    # ── Layer 1: company financial keywords ───────────────────

    def _analyze_by_keywords(self, text: str) -> tuple[Sentiment, float, list[str]]:
        text_lower = text.lower()

        bullish_found = [
            kw for kw in self.bullish_keywords
            if kw.lower() in text_lower and not self._is_negated(text_lower, kw)
        ]
        bearish_found = [
            kw for kw in self.bearish_keywords
            if kw.lower() in text_lower and not self._is_negated(text_lower, kw)
        ]

        negated_bullish = [
            kw for kw in self.bullish_keywords
            if kw.lower() in text_lower and self._is_negated(text_lower, kw)
        ]
        bearish_found.extend(negated_bullish)

        all_found = bearish_found + bullish_found
        if not all_found:
            return Sentiment.NEUTRAL, 0.0, []

        bullish_score = len(bullish_found)
        bearish_score = len(bearish_found) * 1.5

        if bearish_score > bullish_score:
            sentiment = Sentiment.BEARISH
            confidence = min(bearish_score / (bullish_score + bearish_score + 1) + 0.2, 1.0)
        elif bullish_score > bearish_score:
            sentiment = Sentiment.BULLISH
            confidence = bullish_score / (bullish_score + bearish_score + 1)
        else:
            sentiment = Sentiment.NEUTRAL
            confidence = 0.3

        if len(all_found) >= 2:
            confidence = min(confidence + 0.15, 1.0)
        if len(all_found) >= 3:
            confidence = min(confidence + 0.15, 1.0)

        return sentiment, confidence, all_found

    # ── Layer 2: corporate events ─────────────────────────────

    def _analyze_corporate_event(self, text: str) -> tuple[Sentiment, float, list[str], str]:
        """
        Scan for criminal, regulatory, M&A, and debt triggers.
        Returns (sentiment, confidence, keywords_found, event_category).
        """
        text_lower = text.lower()
        best = (Sentiment.NEUTRAL, 0.0, [], "")

        # Criminal/fraud (always bearish, high confidence)
        crim_found = [kw for kw in self.criminal_keywords if kw.lower() in text_lower]
        if crim_found:
            conf = min(self.criminal_confidence + 0.05 * (len(crim_found) - 1), 1.0)
            if conf > best[1]:
                best = (Sentiment.BEARISH, conf, crim_found, "criminal")

        # Regulatory
        for direction in ("bearish", "bullish"):
            sub = self.regulatory_data.get(direction, {})
            kws = sub.get("keywords", [])
            base_conf = sub.get("confidence", 0.6)
            found = [kw for kw in kws if kw.lower() in text_lower]
            if found:
                sent = Sentiment.BEARISH if direction == "bearish" else Sentiment.BULLISH
                conf = min(base_conf + 0.05 * (len(found) - 1), 1.0)
                if conf > best[1]:
                    best = (sent, conf, found, "regulatory")

        # M&A
        for direction in ("bullish", "bearish", "neutral"):
            sub = self.ma_data.get(direction, {})
            kws = sub.get("keywords", [])
            base_conf = sub.get("confidence", 0.5)
            found = [kw for kw in kws if kw.lower() in text_lower]
            if found:
                sent_map = {"bullish": Sentiment.BULLISH, "bearish": Sentiment.BEARISH, "neutral": Sentiment.NEUTRAL}
                sent = sent_map[direction]
                conf = min(base_conf + 0.05 * (len(found) - 1), 1.0)
                if conf > best[1]:
                    best = (sent, conf, found, "ma")

        # Debt
        for direction in ("bearish", "bullish"):
            sub = self.debt_data.get(direction, {})
            kws = sub.get("keywords", [])
            base_conf = sub.get("confidence", 0.6)
            found = [kw for kw in kws if kw.lower() in text_lower]
            if found:
                sent = Sentiment.BEARISH if direction == "bearish" else Sentiment.BULLISH
                conf = min(base_conf + 0.05 * (len(found) - 1), 1.0)
                if conf > best[1]:
                    best = (sent, conf, found, "debt")

        return best

    # ── Layer 3: geopolitical / macro ─────────────────────────

    def _analyze_geopolitical(self, text: str) -> Optional[ParseResult]:
        """
        Scan geo triggers, detect sector context, resolve to tickers.
        Returns ParseResult with multiple tickers, or None.
        """
        text_lower = text.lower()
        best_trigger = None
        best_conf = 0.0

        for trigger_name, trigger_data in self.geo_triggers.items():
            if not isinstance(trigger_data, dict) or "keywords" not in trigger_data:
                continue
            found = [kw for kw in trigger_data["keywords"] if kw.lower() in text_lower]
            if found:
                conf = trigger_data.get("confidence", 0.5)
                conf = min(conf + 0.05 * (len(found) - 1), 1.0)
                if conf > best_conf:
                    best_conf = conf
                    best_trigger = (trigger_name, trigger_data, found, conf)

        if not best_trigger:
            return None

        name, data, keywords, confidence = best_trigger
        sentiment_str = data.get("sentiment", "neutral")
        sentiment = {"bullish": Sentiment.BULLISH, "bearish": Sentiment.BEARISH}.get(sentiment_str, Sentiment.NEUTRAL)
        target_sectors = data.get("sectors", ["market"])

        # Detect sector from text context if target is generic "market"
        if target_sectors == ["market"]:
            detected = self._detect_sector_context(text_lower)
            if detected:
                target_sectors = detected

        tickers = self._sectors_to_tickers(target_sectors)
        figis = [self.ticker_to_figi.get(t, "") for t in tickers]

        return ParseResult(
            success=True,
            tickers=tickers,
            figis=figis,
            sentiment=sentiment,
            confidence=confidence,
            keywords_found=keywords,
            method=AnalysisMethod.GEOPOLITICAL,
            signal_type="sector" if target_sectors != ["market"] else "market",
            reasoning=f"Geo trigger: {name}",
            raw_text="",
        )

    def _detect_sector_context(self, text_lower: str) -> list[str]:
        """Detect which sector(s) a text is about using context clues."""
        matched = []
        for sector_name, sector_data in self.sectors.items():
            if sector_name == "market":
                continue
            clues = sector_data.get("clues", [])
            if any(clue.lower() in text_lower for clue in clues):
                matched.append(sector_name)
        return matched

    def _sectors_to_tickers(self, sector_names: list[str]) -> list[str]:
        seen = set()
        result = []
        for name in sector_names:
            for t in self.sectors.get(name, {}).get("tickers", []):
                if t not in seen:
                    seen.add(t)
                    result.append(t)
        return result

    # ── market relevance detector ─────────────────────────────

    def _is_market_relevant(self, text: str) -> bool:
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in self.market_relevance_keywords)

    # ── spam filter ───────────────────────────────────────────

    def _should_ignore(self, text: str) -> bool:
        text_lower = text.lower()
        return any(kw in text_lower for kw in self.ignore_keywords)

    # ── LLM ───────────────────────────────────────────────────

    def _init_gemini(self):
        if self._gemini_model is not None:
            return True
        if not self.gemini_api_key:
            return False
        try:
            import google.generativeai as genai
            genai.configure(api_key=self.gemini_api_key)
            self._gemini_model = genai.GenerativeModel("gemini-1.5-flash")
            return True
        except ImportError:
            return False
        except Exception:
            return False

    def _analyze_by_llm(self, text: str, known_tickers: list[str]) -> tuple[Sentiment, float, str, list[str], list[str]]:
        """
        LLM analysis with entity resolution and sector identification.
        Returns (sentiment, confidence, reasoning, tickers, sectors).
        """
        if not self._init_gemini():
            return Sentiment.NEUTRAL, 0.0, "LLM unavailable", [], []

        ticker_hint = f"Уже определённые тикеры: {', '.join(known_tickers)}" if known_tickers else "Тикер не определён."

        prompt = f"""Проанализируй новость с точки зрения российского фондового рынка.

Новость:
\"\"\"{text}\"\"\"

{ticker_hint}

Задача:
1. Определи, какая публично торгуемая российская компания (тикер на MOEX) затронута. Учитывай дочерние компании и бренды.
2. Определи тип события: company (корпоративное), geopolitical (геополитическое), regulatory, criminal, ma, debt.
3. Определи sentiment и силу влияния.
4. Укажи затронутые секторы: oil_gas, metals, banks, real_estate, tech, energy, retail, telecom, market.

Ответь СТРОГО в формате JSON:
{{
    "tickers": ["SBER"],
    "sectors": ["banks"],
    "event_type": "company",
    "sentiment": "bullish",
    "confidence": 0.7,
    "reasoning": "краткое объяснение"
}}

Если новость не связана с российским фондовым рынком — верни пустые tickers и neutral с confidence 0."""

        if not self._rate_limiter.wait_if_needed():
            return Sentiment.NEUTRAL, 0.0, "Rate limit (daily cap)", [], []

        import time as _time

        _MAX_LLM_RETRIES = 3
        _BASE_DELAY = 2.0

        for attempt in range(_MAX_LLM_RETRIES):
            try:
                response = self._gemini_model.generate_content(prompt)
                result_text = response.text.strip()

                result = self._extract_json(result_text)
                if result is not None:
                    sentiment_map = {"bullish": Sentiment.BULLISH, "bearish": Sentiment.BEARISH, "neutral": Sentiment.NEUTRAL}
                    sentiment = sentiment_map.get(result.get("sentiment", "neutral"), Sentiment.NEUTRAL)
                    confidence = float(result.get("confidence", 0.0))
                    reasoning = result.get("reasoning", "")
                    llm_tickers = result.get("tickers", [])
                    llm_sectors = result.get("sectors", [])

                    valid_tickers = [t for t in llm_tickers if t in self.ticker_to_figi]
                    if not valid_tickers and llm_sectors:
                        valid_tickers = self._sectors_to_tickers(llm_sectors)

                    return sentiment, confidence, reasoning, valid_tickers, llm_sectors

                # JSON extraction failed — not retryable
                break

            except Exception as e:
                is_retryable = any(
                    name in type(e).__name__
                    for name in ("ResourceExhausted", "ServiceUnavailable", "InternalServerError", "DeadlineExceeded")
                )
                if is_retryable and attempt + 1 < _MAX_LLM_RETRIES:
                    delay = min(_BASE_DELAY * (2 ** attempt), 30.0)
                    _time.sleep(delay)
                    continue
                break

        return Sentiment.NEUTRAL, 0.0, "Analysis failed", [], []

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """Extract a JSON object from LLM output, handling markdown fences
        and nested braces that the old regex missed."""
        cleaned = re.sub(r"```(?:json)?\s*", "", text).strip().rstrip("`")
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            pass

        # Balanced-brace extraction for responses with extra prose
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start != -1:
                    try:
                        return json.loads(text[start : i + 1])
                    except (json.JSONDecodeError, ValueError):
                        start = -1
        return None

    # ── main parse flow ───────────────────────────────────────

    def parse(self, text: str, source: str = "unknown") -> ParseResult:
        """
        Three-layer parse flow.

        1. Filter spam
        2. Resolve entities -> tickers
        3. Layer 1: company financial keywords
        4. Layer 2: corporate events (criminal, regulatory, M&A, debt)
        5. Best of 1-2 -> return if confident with ticker
        6. Layer 3: geopolitical/macro -> sector routing
        7. Market relevance -> LLM fallback
        """
        if self._should_ignore(text):
            return ParseResult(success=False, method=AnalysisMethod.FAILED, reasoning="Ignored (spam/ad)", raw_text=text)

        # Entity resolution
        tickers = self._resolve_entities(text)
        figis = [self.ticker_to_figi.get(t, "") for t in tickers]

        # Layer 1: company financial keywords
        kw_sentiment, kw_confidence, kw_found = self._analyze_by_keywords(text)

        # Layer 2: corporate events
        corp_sentiment, corp_confidence, corp_found, corp_category = self._analyze_corporate_event(text)

        # Pick best of layers 1-2
        if corp_confidence > kw_confidence and corp_found:
            best_sentiment = corp_sentiment
            best_confidence = corp_confidence
            best_keywords = corp_found
            best_method = AnalysisMethod.CORPORATE
            best_signal_type = "corporate"
            best_reasoning = f"Corporate event ({corp_category}): {', '.join(corp_found)}"
        elif kw_found:
            best_sentiment = kw_sentiment
            best_confidence = kw_confidence
            best_keywords = kw_found
            best_method = AnalysisMethod.KEYWORD
            best_signal_type = "company"
            best_reasoning = f"Keywords: {', '.join(kw_found)}"
        else:
            best_sentiment = Sentiment.NEUTRAL
            best_confidence = 0.0
            best_keywords = []
            best_method = AnalysisMethod.FAILED
            best_signal_type = "company"
            best_reasoning = None

        # Return if confident with at least one ticker
        if best_confidence >= self.min_keyword_confidence and tickers:
            return ParseResult(
                success=True, tickers=tickers, figis=figis,
                sentiment=best_sentiment, confidence=best_confidence,
                keywords_found=best_keywords, method=best_method,
                signal_type=best_signal_type, reasoning=best_reasoning, raw_text=text,
            )

        # If we have specific tickers AND company/corporate keywords, prefer
        # the ticker-specific result over a sector-wide geo sweep. Still require
        # a minimum confidence floor to avoid noise.
        _MIN_FLOOR = 0.4
        if best_keywords and tickers and best_confidence >= _MIN_FLOOR:
            return ParseResult(
                success=True, tickers=tickers, figis=figis,
                sentiment=best_sentiment, confidence=best_confidence,
                keywords_found=best_keywords, method=best_method,
                signal_type=best_signal_type,
                reasoning=best_reasoning, raw_text=text,
            )

        # Layer 3: geopolitical/macro (only when no specific ticker+keyword combo)
        geo_result = self._analyze_geopolitical(text)
        if geo_result and geo_result.confidence >= 0.4:
            geo_result.raw_text = text
            if tickers:
                merged = list(dict.fromkeys(tickers + geo_result.tickers))
                geo_result.tickers = merged
                geo_result.figis = [self.ticker_to_figi.get(t, "") for t in merged]
            return geo_result

        # LLM fallback: fire when text is market-relevant
        if self.use_llm_fallback and (tickers or best_keywords or self._is_market_relevant(text)):
            llm_sent, llm_conf, reasoning, llm_tickers, llm_sectors = self._analyze_by_llm(text, tickers)

            if llm_conf >= _MIN_FLOOR:
                final_tickers = llm_tickers or tickers
                final_figis = [self.ticker_to_figi.get(t, "") for t in final_tickers]

                if best_keywords and best_sentiment == llm_sent:
                    final_conf = min((best_confidence + llm_conf) / 1.5, 1.0)
                    method = AnalysisMethod.HYBRID
                else:
                    final_conf = llm_conf
                    method = AnalysisMethod.SEMANTIC

                signal_type = "sector" if llm_sectors and not llm_tickers else "company"

                return ParseResult(
                    success=True, tickers=final_tickers, figis=final_figis,
                    sentiment=llm_sent, confidence=final_conf,
                    keywords_found=best_keywords, method=method,
                    signal_type=signal_type, reasoning=reasoning, raw_text=text,
                )

        # Nothing found
        return ParseResult(
            success=False, tickers=tickers, figis=figis,
            sentiment=Sentiment.NEUTRAL, confidence=0.0,
            method=AnalysisMethod.FAILED, reasoning="No relevant signals found", raw_text=text,
        )

    # ── conversion to NewsEvent(s) ────────────────────────────

    def to_news_events(self, result: ParseResult, source: str = "parser") -> list[NewsEvent]:
        """Generate one NewsEvent per ticker from a ParseResult."""
        if not result.success or not result.tickers:
            return []
        events = []
        for ticker in result.tickers:
            figi = self.ticker_to_figi.get(ticker, "")
            if not figi:
                continue
            events.append(NewsEvent(
                ticker=ticker,
                figi=figi,
                sentiment=result.sentiment,
                confidence=result.confidence,
                keywords_found=result.keywords_found,
                timestamp=datetime.now(),
                source=source,
                raw_text=result.raw_text,
            ))
        return events

    def to_news_event(self, result: ParseResult, source: str = "parser") -> Optional[NewsEvent]:
        """Backward-compatible: return first NewsEvent or None."""
        events = self.to_news_events(result, source)
        return events[0] if events else None
