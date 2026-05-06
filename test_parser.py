#!/usr/bin/env python3
"""
Тест NewsParser — гибридный анализ новостей.

Запуск:
    python test_parser.py                    # Интерактивный режим
    python test_parser.py "Сбер рекомендует дивиденды"  # Одноразовый анализ
    
Для использования Gemini (семантика):
    export GEMINI_API_KEY=your-key
"""

import sys
from bot.listeners import NewsParser, ParseResult
from bot.models import Sentiment


def print_result(result: ParseResult):
    """Красивый вывод результата."""
    print("\n" + "="*60)
    print("PARSE RESULT")
    print("="*60)
    
    status = "✅ SUCCESS" if result.success else "❌ FAILED"
    print(f"Status: {status}")
    
    if result.tickers:
        print(f"Tickers: {', '.join(result.tickers)}")
    else:
        print("Ticker: not found")
    
    sentiment_emoji = {
        Sentiment.BULLISH: "🟢 BULLISH",
        Sentiment.BEARISH: "🔴 BEARISH",
        Sentiment.NEUTRAL: "⚪ NEUTRAL"
    }
    print(f"Sentiment: {sentiment_emoji[result.sentiment]}")
    print(f"Confidence: {result.confidence:.1%}")
    print(f"Method: {result.method.value}")
    print(f"Signal type: {result.signal_type}")
    
    if result.keywords_found:
        print(f"Keywords: {', '.join(result.keywords_found)}")
    
    if result.reasoning:
        print(f"Reasoning: {result.reasoning}")
    
    print("="*60 + "\n")


def test_examples(parser: NewsParser):
    """Тест на примерах."""
    
    examples = [
        # Bullish (company)
        "Сбербанк рекомендует дивиденды в размере 33 рубля на акцию",
        "GAZP: Газпром сообщил о рекордной прибыли за 2024 год",
        "Совет директоров Лукойла одобрил программу байбэка на 100 млрд рублей",
        "Норникель: выручка выросла на 25% год к году",
        
        # Bearish (company)
        "Сбер отменил выплату дивидендов за 2024 год",
        "Газпром получил убыток 500 млрд рублей",
        "Яндекс объявил о допэмиссии акций",
        
        # Criminal / corporate (Layer 2)
        "Руководство ГК Самолёт задержано по делу об обналичивании",
        "ФАС возбудила дело о нарушении антимонопольного законодательства против Магнита",
        
        # Geopolitical / sector (Layer 3)
        "ЕС ввёл нефтяное эмбарго против России",
        "Иран пригрозил заблокировать Ормузский пролив",
        "ЦБ повысил ключевую ставку до 21%",
        "Стороны договорились о прекращении огня",
        
        # Entity resolution via entities.json
        "MAX мессенджер обогнал Telegram по числу пользователей в РФ",
        "Кинопоиск запустил новый оригинальный сериал",
        "Ozon Банк получил лицензию на брокерскую деятельность",
        
        # Neutral / Unclear
        "Сбербанк открыл новый офис в Москве",
        "Погода в Москве: ожидается снег",
        
        # Mixed signals
        "Несмотря на санкции, Северсталь показала рост прибыли",
    ]
    
    print("\n" + "="*60)
    print("TESTING EXAMPLES")
    print("="*60)
    
    for text in examples:
        print(f"\n📝 Input: {text[:60]}...")
        result = parser.parse(text)
        
        emoji = {
            Sentiment.BULLISH: "🟢",
            Sentiment.BEARISH: "🔴",
            Sentiment.NEUTRAL: "⚪"
        }
        
        tickers_str = ", ".join(result.tickers[:3]) if result.tickers else "?"
        if len(result.tickers) > 3:
            tickers_str += f" +{len(result.tickers) - 3}"
        
        print(f"   {emoji[result.sentiment]} {result.sentiment.value:8} | "
              f"{result.confidence:.0%} | {result.method.value:12} | [{tickers_str}] {result.signal_type}")
        
        if result.keywords_found:
            print(f"   Keywords: {', '.join(result.keywords_found)}")


def interactive_mode(parser: NewsParser):
    """Интерактивный режим."""
    
    print("\n" + "="*60)
    print("INTERACTIVE MODE")
    print("Type news text to analyze, 'examples' to run tests, 'quit' to exit")
    print("="*60)
    
    while True:
        try:
            text = input("\n📝 Enter text: ").strip()
            
            if not text:
                continue
            
            if text.lower() in ('quit', 'exit', 'q'):
                break
            
            if text.lower() == 'examples':
                test_examples(parser)
                continue
            
            result = parser.parse(text)
            print_result(result)
            
        except KeyboardInterrupt:
            print("\n")
            break
        except EOFError:
            break
    
    print("👋 Bye!")


def main():
    # Инициализация парсера
    parser = NewsParser(use_llm_fallback=True)
    
    print("\n🔍 NewsParser initialized")
    print(f"   Bullish keywords: {len(parser.bullish_keywords)}")
    print(f"   Bearish keywords: {len(parser.bearish_keywords)}")
    print(f"   Ticker synonyms: {len(parser.ticker_synonyms)}")
    print(f"   Entity map: {len(parser.entity_map)} aliases")
    print(f"   Sectors: {len(parser.sectors)}")
    print(f"   Gemini API: {'✅ configured' if parser.gemini_api_key else '❌ not set'}")
    
    if len(sys.argv) > 1:
        # Одноразовый анализ
        text = " ".join(sys.argv[1:])
        result = parser.parse(text)
        print_result(result)
    else:
        # Интерактивный режим
        interactive_mode(parser)


if __name__ == "__main__":
    main()

