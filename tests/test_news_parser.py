"""Tests for bot.listeners.news_parser – NewsParser and ParseResult."""

import json
from pathlib import Path

import pytest

from bot.listeners.news_parser import AnalysisMethod, NewsParser, ParseResult
from bot.models import NewsEvent, Sentiment


# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture()
def parser(tmp_data_dir):
    """NewsParser backed by the minimal test-fixture data files."""
    return NewsParser(data_dir=tmp_data_dir, use_llm_fallback=False)


@pytest.fixture()
def parser_with_corporate(tmp_path):
    """NewsParser whose keywords.json has criminal/regulatory/ma/debt at the
    top level so that _analyze_corporate_event is exercised properly.

    The test fixture stores these under 'corporate_events', but the parser
    reads them from the top level.
    """
    # Build a keywords.json that the parser actually expects
    kw = {
        "bullish": {
            "keywords": [
                "рекомендует дивиденд",
                "прибыль выросла",
                "рост выручки",
                "байбэк",
                "buyback",
            ],
            "weight": 1.0,
        },
        "bearish": {
            "keywords": [
                "убыток",
                "допэмисси",
                "падение прибыли",
                "делистинг",
                "банкротство",
            ],
            "weight": -1.0,
        },
        "ignore": {"keywords": ["реклама", "промокод"]},
        "geopolitical": {
            "sanctions_bearish": {
                "keywords": ["санкци", "эмбарго"],
                "sentiment": "bearish",
                "sectors": ["market"],
                "confidence": 0.6,
            },
            "peace_deescalation": {
                "keywords": ["перемири", "мирные переговор"],
                "sentiment": "bullish",
                "sectors": ["market"],
                "confidence": 0.6,
            },
        },
        "criminal": {
            "keywords": ["задержан", "арестован", "уголовное дело"],
            "confidence": 0.75,
        },
        "regulatory": {
            "bullish": {
                "keywords": ["лицензия получена", "одобрение регулятора"],
                "confidence": 0.65,
            },
            "bearish": {
                "keywords": ["штраф", "отзыв лицензии"],
                "confidence": 0.7,
            },
        },
        "ma": {
            "bullish": {
                "keywords": ["поглощение", "слияние", "покупка актив"],
                "confidence": 0.65,
            },
        },
        "debt": {
            "bearish": {
                "keywords": ["реструктуризация долга", "технический дефолт"],
                "confidence": 0.75,
            },
        },
        "market_relevance": {
            "keywords": ["акции", "биржа", "рынок"],
        },
    }
    (tmp_path / "keywords.json").write_text(
        json.dumps(kw, ensure_ascii=False), encoding="utf-8"
    )

    # Copy other fixture files from the canonical fixtures directory
    from tests.conftest import FIXTURES_DIR

    for name in ("entities.json", "whitelist.json", "sectors.json"):
        src = FIXTURES_DIR / name
        (tmp_path / name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")

    return NewsParser(data_dir=tmp_path, use_llm_fallback=False)


# ══════════════════════════════════════════════════════════════════
# ParseResult dataclass
# ══════════════════════════════════════════════════════════════════


class TestParseResult:
    def test_ticker_property_returns_first(self):
        r = ParseResult(success=True, tickers=["SBER", "GAZP"])
        assert r.ticker == "SBER"

    def test_ticker_property_none_when_empty(self):
        r = ParseResult(success=True, tickers=[])
        assert r.ticker is None

    def test_figi_property_returns_first(self):
        r = ParseResult(success=True, figis=["BBG004730N88", "BBG004730RP0"])
        assert r.figi == "BBG004730N88"

    def test_figi_property_none_when_empty(self):
        r = ParseResult(success=True, figis=[])
        assert r.figi is None

    def test_defaults(self):
        r = ParseResult(success=False)
        assert r.sentiment == Sentiment.NEUTRAL
        assert r.confidence == 0.0
        assert r.keywords_found == []
        assert r.method == AnalysisMethod.FAILED
        assert r.signal_type == "company"
        assert r.reasoning is None


# ══════════════════════════════════════════════════════════════════
# _should_ignore
# ══════════════════════════════════════════════════════════════════


class TestShouldIgnore:
    def test_text_with_ignore_keyword(self, parser):
        assert parser._should_ignore("Это реклама нового продукта") is True

    def test_text_with_promokod(self, parser):
        assert parser._should_ignore("Используйте промокод SAVE20") is True

    def test_normal_text_not_ignored(self, parser):
        assert parser._should_ignore("Прибыль Сбербанка выросла на 20%") is False

    def test_empty_text(self, parser):
        assert parser._should_ignore("") is False

    def test_case_insensitive_ignore(self, parser):
        # ignore keywords are compared against text.lower()
        assert parser._should_ignore("РЕКЛАМА новинки") is True


# ══════════════════════════════════════════════════════════════════
# _is_market_relevant
# ══════════════════════════════════════════════════════════════════


class TestIsMarketRelevant:
    """The test fixture keywords.json has no top-level 'market_relevance' key,
    so the base ``parser`` will have an empty list.  We use
    ``parser_with_corporate`` which includes those keywords."""

    def test_relevant_text(self, parser_with_corporate):
        assert parser_with_corporate._is_market_relevant("акции Сбера растут") is True

    def test_relevant_birzha(self, parser_with_corporate):
        # Keyword "биржа" must appear as-is (substring match)
        assert parser_with_corporate._is_market_relevant("торги на биржа сегодня") is True

    def test_irrelevant_text(self, parser_with_corporate):
        assert parser_with_corporate._is_market_relevant("погода в Москве") is False

    def test_empty_market_relevance_list(self, parser):
        # base fixture has no market_relevance -> always False
        assert parser._is_market_relevant("акции Сбера растут") is False


# ══════════════════════════════════════════════════════════════════
# _is_negated
# ══════════════════════════════════════════════════════════════════


class TestIsNegated:
    def test_negated_with_ne(self, parser):
        assert parser._is_negated("прибыль не выросла в этом квартале", "выросла") is True

    def test_negated_with_bez(self, parser):
        assert parser._is_negated("без роста выручки", "роста") is True

    def test_not_negated(self, parser):
        assert parser._is_negated("прибыль выросла на 30%", "выросла") is False

    def test_negation_window_too_far(self, parser):
        # Negation marker more than 30 chars before keyword -> not negated
        filler = "x" * 35
        text = f"не {filler}выросла"
        assert parser._is_negated(text, "выросла") is False

    def test_negation_right_at_boundary(self, parser):
        # Place "не " just within the 30-char window before keyword
        # "не " = 3 chars, then we need filler so that 'не ' ends within window
        filler = "a" * 24  # 3 (не ) + 24 = 27 chars before keyword
        text = f"не {filler}выросла"
        assert parser._is_negated(text, "выросла") is True

    def test_keyword_not_present(self, parser):
        assert parser._is_negated("не выросла", "убыток") is False

    def test_negated_with_net(self, parser):
        assert parser._is_negated("нет роста прибыли", "роста") is True


# ══════════════════════════════════════════════════════════════════
# _resolve_entities
# ══════════════════════════════════════════════════════════════════


class TestResolveEntities:
    def test_sbermarket_resolves_to_sber(self, parser):
        result = parser._resolve_entities("СберМаркет показал рост")
        assert "SBER" in result

    def test_kinopoisk_resolves_to_ydex(self, parser):
        result = parser._resolve_entities("Кинопоиск запустил новый сериал")
        assert "YDEX" in result

    def test_gazprom_neft_resolves_to_gazp(self, parser):
        result = parser._resolve_entities("Газпром нефть увеличила добычу")
        assert "GAZP" in result

    def test_no_match_returns_empty(self, parser):
        result = parser._resolve_entities("Погода в Москве улучшилась")
        assert result == []

    def test_ticker_synonym_sber(self, parser):
        result = parser._resolve_entities("Сбербанк отчитался за квартал")
        assert "SBER" in result

    def test_ticker_synonym_gazprom(self, parser):
        result = parser._resolve_entities("Газпром снизил поставки")
        assert "GAZP" in result

    def test_regex_ticker_in_whitelist(self, parser):
        result = parser._resolve_entities("Акции SBER выросли на 5%")
        assert "SBER" in result

    def test_regex_ticker_not_in_whitelist(self, parser):
        result = parser._resolve_entities("Акции ZZZZ упали")
        assert "ZZZZ" not in result

    def test_entity_priority_over_synonym(self, parser):
        # Both entity map and synonym can match SBER; entity (priority 3)
        # should rank higher than synonym (priority 2)
        result = parser._resolve_entities("СберМаркет и Сбербанк")
        assert result[0] == "SBER"

    def test_multiple_entities_resolved(self, parser):
        result = parser._resolve_entities("Сбербанк и Газпром отчитались")
        assert "SBER" in result
        assert "GAZP" in result

    def test_okko_resolves_to_sber(self, parser):
        result = parser._resolve_entities("Okko запустил новый фильм")
        assert "SBER" in result


# ══════════════════════════════════════════════════════════════════
# _word_match (static method)
# ══════════════════════════════════════════════════════════════════


class TestWordMatch:
    def test_long_needle_substring(self):
        # needles >= 4 chars: simple substring
        assert NewsParser._word_match("sber", "акции sber выросли") is True

    def test_long_needle_not_found(self):
        assert NewsParser._word_match("sber", "акции gazp выросли") is False

    def test_short_needle_at_word_boundary(self):
        # "вк" (2 chars) at word boundary
        assert NewsParser._word_match("вк", "вк запустил сервис") is True

    def test_short_needle_inside_word_rejected(self):
        # "вк" inside "ставку" should NOT match
        assert NewsParser._word_match("вк", "повысили ставку") is False

    def test_short_needle_at_end(self):
        assert NewsParser._word_match("вк", "акции вк") is True

    def test_short_needle_surrounded_by_non_alpha(self):
        assert NewsParser._word_match("вк", "(вк)") is True


# ══════════════════════════════════════════════════════════════════
# _analyze_by_keywords
# ══════════════════════════════════════════════════════════════════


class TestAnalyzeByKeywords:
    def test_bullish_keyword(self, parser):
        sentiment, conf, found = parser._analyze_by_keywords(
            "Прибыль выросла на 30% за квартал"
        )
        assert sentiment == Sentiment.BULLISH
        assert conf > 0
        assert "прибыль выросла" in [kw.lower() for kw in found]

    def test_bearish_keyword(self, parser):
        sentiment, conf, found = parser._analyze_by_keywords(
            "Компания зафиксировала убыток по итогам года"
        )
        assert sentiment == Sentiment.BEARISH
        assert conf > 0
        assert any("убыток" in kw.lower() for kw in found)

    def test_no_keywords_neutral(self, parser):
        sentiment, conf, found = parser._analyze_by_keywords(
            "Погода в Москве солнечная"
        )
        assert sentiment == Sentiment.NEUTRAL
        assert conf == 0.0
        assert found == []

    def test_negated_bullish_becomes_bearish(self, parser):
        # Keyword "рост выручки" must appear as contiguous substring
        # then "не" before it triggers negation -> bearish
        sentiment, conf, found = parser._analyze_by_keywords(
            "Компания показала не рост выручки а стагнацию"
        )
        # "рост выручки" is negated -> treated as bearish
        assert sentiment == Sentiment.BEARISH
        assert conf > 0

    def test_multiple_bullish_keywords_boost_confidence(self, parser):
        text_single = "Прибыль выросла в этом квартале"
        text_double = "Прибыль выросла, рост выручки ускорился, байбэк объявлен"
        _, conf_single, _ = parser._analyze_by_keywords(text_single)
        _, conf_double, _ = parser._analyze_by_keywords(text_double)
        assert conf_double > conf_single

    def test_buyback_english_bullish(self, parser):
        sentiment, conf, found = parser._analyze_by_keywords(
            "The company announced a buyback program"
        )
        assert sentiment == Sentiment.BULLISH
        assert any("buyback" in kw.lower() for kw in found)

    def test_bearish_weight_advantage(self, parser):
        # bearish_score = count * 1.5, so one bearish can outweigh one bullish
        sentiment, _, _ = parser._analyze_by_keywords(
            "Прибыль выросла, но убыток по другому направлению"
        )
        assert sentiment == Sentiment.BEARISH

    def test_dopemission_bearish(self, parser):
        sentiment, conf, found = parser._analyze_by_keywords(
            "Компания объявила допэмиссию акций"
        )
        assert sentiment == Sentiment.BEARISH
        assert conf > 0


# ══════════════════════════════════════════════════════════════════
# _analyze_corporate_event
# ══════════════════════════════════════════════════════════════════


class TestAnalyzeCorporateEvent:
    def test_criminal_keyword_bearish(self, parser_with_corporate):
        sentiment, conf, found, category = (
            parser_with_corporate._analyze_corporate_event(
                "Директор компании задержан по подозрению в мошенничестве"
            )
        )
        assert sentiment == Sentiment.BEARISH
        assert conf >= 0.7
        assert "задержан" in found
        assert category == "criminal"

    def test_multiple_criminal_keywords_boost(self, parser_with_corporate):
        _, conf_single, _, _ = parser_with_corporate._analyze_corporate_event(
            "Директор задержан"
        )
        _, conf_double, _, _ = parser_with_corporate._analyze_corporate_event(
            "Директор задержан, возбуждено уголовное дело"
        )
        assert conf_double > conf_single

    def test_regulatory_bearish(self, parser_with_corporate):
        sentiment, conf, found, category = (
            parser_with_corporate._analyze_corporate_event(
                "Компания получила крупный штраф от регулятора"
            )
        )
        assert sentiment == Sentiment.BEARISH
        assert conf >= 0.65
        assert category == "regulatory"

    def test_debt_bearish(self, parser_with_corporate):
        sentiment, conf, found, category = (
            parser_with_corporate._analyze_corporate_event(
                "Объявлен технический дефолт по облигациям"
            )
        )
        assert sentiment == Sentiment.BEARISH
        assert conf >= 0.7
        assert category == "debt"

    def test_no_corporate_event_neutral(self, parser_with_corporate):
        sentiment, conf, found, category = (
            parser_with_corporate._analyze_corporate_event(
                "Погода в Москве солнечная"
            )
        )
        assert sentiment == Sentiment.NEUTRAL
        assert conf == 0.0
        assert found == []
        assert category == ""

    def test_no_corporate_data_in_base_fixture(self, parser):
        # The base fixture has criminal/regulatory etc. nested under
        # corporate_events, which the parser does not read -> always neutral.
        sentiment, conf, found, category = parser._analyze_corporate_event(
            "Директор задержан"
        )
        assert sentiment == Sentiment.NEUTRAL
        assert found == []


# ══════════════════════════════════════════════════════════════════
# _analyze_geopolitical
# ══════════════════════════════════════════════════════════════════


class TestAnalyzeGeopolitical:
    def test_sanctions_bearish(self, parser):
        result = parser._analyze_geopolitical("Введены новые санкции против РФ")
        assert result is not None
        assert result.sentiment == Sentiment.BEARISH
        assert result.confidence >= 0.5
        assert result.method == AnalysisMethod.GEOPOLITICAL

    def test_peace_bullish(self, parser):
        result = parser._analyze_geopolitical(
            "Стороны достигли перемирия на переговорах"
        )
        assert result is not None
        assert result.sentiment == Sentiment.BULLISH

    def test_no_geo_trigger_returns_none(self, parser):
        result = parser._analyze_geopolitical("Прибыль Сбера выросла")
        assert result is None

    def test_oil_embargo_targets_oil_gas_sector(self, parser):
        result = parser._analyze_geopolitical(
            "Введено нефтяное эмбарго на российскую нефть"
        )
        assert result is not None
        assert result.sentiment == Sentiment.BEARISH
        # The "sanctions_oil" trigger specifies sectors: ["oil_gas"]
        assert "ROSN" in result.tickers or "LKOH" in result.tickers

    def test_sector_context_detection_in_geo(self, parser):
        # Generic "sanctions_bearish" has sectors: ["market"], but text has
        # oil clue -> should detect oil_gas sector
        result = parser._analyze_geopolitical(
            "Новые санкции ударят по нефтяному сектору"
        )
        assert result is not None
        # Should resolve to oil_gas tickers via sector context
        assert any(t in result.tickers for t in ["ROSN", "LKOH", "GAZP"])


# ══════════════════════════════════════════════════════════════════
# _extract_json
# ══════════════════════════════════════════════════════════════════


class TestExtractJson:
    def test_clean_json(self):
        raw = '{"sentiment": "bullish", "confidence": 0.8}'
        result = NewsParser._extract_json(raw)
        assert result == {"sentiment": "bullish", "confidence": 0.8}

    def test_fenced_json(self):
        raw = '```json\n{"sentiment": "bearish"}\n```'
        result = NewsParser._extract_json(raw)
        assert result == {"sentiment": "bearish"}

    def test_fenced_without_lang(self):
        raw = '```\n{"key": "value"}\n```'
        result = NewsParser._extract_json(raw)
        assert result == {"key": "value"}

    def test_invalid_json_returns_none(self):
        assert NewsParser._extract_json("not json at all") is None

    def test_json_with_surrounding_prose(self):
        raw = 'Here is the result: {"tickers": ["SBER"]} done.'
        result = NewsParser._extract_json(raw)
        assert result == {"tickers": ["SBER"]}

    def test_nested_braces(self):
        raw = '{"outer": {"inner": 1}}'
        result = NewsParser._extract_json(raw)
        assert result == {"outer": {"inner": 1}}

    def test_empty_string(self):
        assert NewsParser._extract_json("") is None


# ══════════════════════════════════════════════════════════════════
# _detect_sector_context
# ══════════════════════════════════════════════════════════════════


class TestDetectSectorContext:
    def test_oil_sector_detected(self, parser):
        result = parser._detect_sector_context("нефтяной сектор показал рост")
        assert "oil_gas" in result

    def test_bank_sector_detected(self, parser):
        result = parser._detect_sector_context("банковский сектор под давлением")
        assert "banks" in result

    def test_no_sector_detected(self, parser):
        result = parser._detect_sector_context("погода в москве улучшилась")
        assert result == []

    def test_multiple_sectors(self, parser):
        result = parser._detect_sector_context("нефть и банки под санкциями")
        assert "oil_gas" in result
        assert "banks" in result


# ══════════════════════════════════════════════════════════════════
# _sectors_to_tickers
# ══════════════════════════════════════════════════════════════════


class TestSectorsToTickers:
    def test_oil_gas_tickers(self, parser):
        result = parser._sectors_to_tickers(["oil_gas"])
        assert result == ["ROSN", "LKOH", "GAZP"]

    def test_banks_tickers(self, parser):
        result = parser._sectors_to_tickers(["banks"])
        assert result == ["SBER"]

    def test_unknown_sector_empty(self, parser):
        result = parser._sectors_to_tickers(["nonexistent_sector"])
        assert result == []

    def test_multiple_sectors_no_duplicates(self, parser):
        # oil_gas has GAZP, and if another sector also had GAZP it would be deduped
        result = parser._sectors_to_tickers(["oil_gas", "banks"])
        assert "ROSN" in result
        assert "SBER" in result
        # no duplicates
        assert len(result) == len(set(result))

    def test_empty_sector_list(self, parser):
        result = parser._sectors_to_tickers([])
        assert result == []


# ══════════════════════════════════════════════════════════════════
# parse (integration)
# ══════════════════════════════════════════════════════════════════


class TestParse:
    def test_bullish_company_news_with_ticker(self, parser):
        # Keyword "прибыль выросла" must be contiguous in the text
        result = parser.parse(
            "У Сбербанка прибыль выросла на 30% за квартал", source="test"
        )
        assert result.success is True
        assert result.sentiment == Sentiment.BULLISH
        assert "SBER" in result.tickers
        assert result.method in (AnalysisMethod.KEYWORD, AnalysisMethod.HYBRID)

    def test_bearish_news(self, parser):
        result = parser.parse(
            "Газпром зафиксировал крупный убыток по итогам года", source="rss"
        )
        assert result.success is True
        assert result.sentiment == Sentiment.BEARISH
        assert "GAZP" in result.tickers

    def test_spam_ignored(self, parser):
        result = parser.parse("Это реклама нового продукта", source="telegram")
        assert result.success is False
        assert result.method == AnalysisMethod.FAILED
        assert "Ignored" in (result.reasoning or "")

    def test_no_signal_returns_failure(self, parser):
        result = parser.parse("Погода в Москве солнечная", source="test")
        assert result.success is False

    def test_raw_text_preserved(self, parser):
        text = "Прибыль Сбербанка выросла"
        result = parser.parse(text, source="test")
        assert result.raw_text == text

    def test_geopolitical_news_parsed(self, parser):
        result = parser.parse(
            "Новые санкции ударят по нефтяному сектору", source="rss"
        )
        assert result.success is True
        assert result.sentiment == Sentiment.BEARISH
        assert result.method == AnalysisMethod.GEOPOLITICAL

    def test_entity_resolution_in_parse(self, parser):
        result = parser.parse(
            "СберМаркет показал рост выручки", source="test"
        )
        assert result.success is True
        assert "SBER" in result.tickers

    def test_figis_populated(self, parser):
        result = parser.parse(
            "Прибыль Сбербанка выросла за квартал", source="test"
        )
        if result.success and result.tickers:
            # SBER figi should be present
            assert "BBG004730N88" in result.figis


# ══════════════════════════════════════════════════════════════════
# to_news_events / to_news_event
# ══════════════════════════════════════════════════════════════════


class TestToNewsEvents:
    def test_success_with_known_tickers(self, parser):
        result = ParseResult(
            success=True,
            tickers=["SBER"],
            figis=["BBG004730N88"],
            sentiment=Sentiment.BULLISH,
            confidence=0.8,
            keywords_found=["прибыль выросла"],
            method=AnalysisMethod.KEYWORD,
            raw_text="Прибыль Сбера выросла",
        )
        events = parser.to_news_events(result, source="test")
        assert len(events) == 1
        assert isinstance(events[0], NewsEvent)
        assert events[0].ticker == "SBER"
        assert events[0].figi == "BBG004730N88"
        assert events[0].sentiment == Sentiment.BULLISH
        assert events[0].source == "test"

    def test_failed_result_returns_empty(self, parser):
        result = ParseResult(success=False)
        events = parser.to_news_events(result, source="test")
        assert events == []

    def test_no_tickers_returns_empty(self, parser):
        result = ParseResult(success=True, tickers=[], sentiment=Sentiment.BULLISH)
        events = parser.to_news_events(result, source="test")
        assert events == []

    def test_ticker_without_figi_skipped(self, parser):
        # ZZZZ is not in whitelist -> no figi -> skipped
        result = ParseResult(
            success=True,
            tickers=["ZZZZ"],
            sentiment=Sentiment.BULLISH,
            confidence=0.8,
            raw_text="test",
        )
        events = parser.to_news_events(result, source="test")
        assert events == []

    def test_multiple_tickers_produce_multiple_events(self, parser):
        result = ParseResult(
            success=True,
            tickers=["SBER", "GAZP"],
            figis=["BBG004730N88", "BBG004730RP0"],
            sentiment=Sentiment.BEARISH,
            confidence=0.7,
            keywords_found=["убыток"],
            method=AnalysisMethod.KEYWORD,
            raw_text="Убытки по всему сектору",
        )
        events = parser.to_news_events(result, source="rss")
        assert len(events) == 2
        tickers = {e.ticker for e in events}
        assert tickers == {"SBER", "GAZP"}

    def test_to_news_event_returns_first(self, parser):
        result = ParseResult(
            success=True,
            tickers=["SBER", "GAZP"],
            figis=["BBG004730N88", "BBG004730RP0"],
            sentiment=Sentiment.BULLISH,
            confidence=0.8,
            keywords_found=["прибыль выросла"],
            method=AnalysisMethod.KEYWORD,
            raw_text="test",
        )
        event = parser.to_news_event(result, source="test")
        assert event is not None
        assert event.ticker == "SBER"

    def test_to_news_event_none_on_failure(self, parser):
        result = ParseResult(success=False)
        assert parser.to_news_event(result, source="test") is None

    def test_events_have_keywords_and_confidence(self, parser):
        result = ParseResult(
            success=True,
            tickers=["YDEX"],
            figis=["TCS00A107T19"],
            sentiment=Sentiment.BULLISH,
            confidence=0.75,
            keywords_found=["рост выручки"],
            method=AnalysisMethod.KEYWORD,
            raw_text="Рост выручки Яндекса",
        )
        events = parser.to_news_events(result, source="rss")
        assert len(events) == 1
        assert events[0].confidence == 0.75
        assert events[0].keywords_found == ["рост выручки"]


# ══════════════════════════════════════════════════════════════════
# Constructor / data loading
# ══════════════════════════════════════════════════════════════════


class TestNewsParserInit:
    def test_loads_bullish_keywords(self, parser):
        assert len(parser.bullish_keywords) > 0
        assert "прибыль выросла" in parser.bullish_keywords

    def test_loads_bearish_keywords(self, parser):
        assert len(parser.bearish_keywords) > 0
        assert "убыток" in parser.bearish_keywords

    def test_loads_ignore_keywords(self, parser):
        assert "реклама" in parser.ignore_keywords

    def test_loads_entity_map(self, parser):
        assert "сбермаркет" in parser.entity_map
        assert parser.entity_map["сбермаркет"] == "SBER"

    def test_loads_ticker_to_figi(self, parser):
        assert parser.ticker_to_figi["SBER"] == "BBG004730N88"
        assert parser.ticker_to_figi["GAZP"] == "BBG004730RP0"

    def test_loads_sectors(self, parser):
        assert "oil_gas" in parser.sectors
        assert "banks" in parser.sectors

    def test_missing_data_dir_no_crash(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        p = NewsParser(data_dir=empty_dir, use_llm_fallback=False)
        assert p.bullish_keywords == []
        assert p.entity_map == {}
        assert p.ticker_to_figi == {}

    def test_llm_fallback_disabled(self, parser):
        assert parser.use_llm_fallback is False
