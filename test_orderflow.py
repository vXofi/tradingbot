"""
Тест модуля Order Flow.

Запуск:
    python test_orderflow.py SBER
    python test_orderflow.py GAZP
"""

import asyncio
import sys
from datetime import datetime

from t_tech.invest import Client

from bot.config import Config
from bot.models import Sentiment
from bot.validators import FlowAnalyzer


async def test_orderflow(ticker: str):
    """Тестирование Order Flow анализа."""
    
    print(f"\n{'='*60}")
    print(f"Order Flow Analysis: {ticker}")
    print(f"{'='*60}\n")
    
    # Загружаем конфиг
    config = Config.from_env()
    
    # Получаем FIGI
    figi = config.get_figi(ticker)
    if not figi:
        print(f"❌ Тикер {ticker} не найден в whitelist")
        print(f"   Доступные: {list(config.whitelist.keys())[:10]}...")
        return
    
    print(f"📊 Тикер: {ticker}")
    print(f"📊 FIGI: {figi}")
    print(f"📊 Sandbox: {config.use_sandbox}")
    print()
    
    # Подключаемся
    with Client(config.tinkoff_token) as client:
        analyzer = FlowAnalyzer(client, config)
        
        print("🔄 Получение данных стакана...")
        
        # Анализ
        analysis = await analyzer.analyze(figi)
        
        print()
        print("📈 РЕЗУЛЬТАТ АНАЛИЗА:")
        print(f"   Время: {analysis.timestamp.strftime('%H:%M:%S')}")
        print()
        
        # Стакан
        snapshot = analyzer.orderbook.get_latest(figi)
        if snapshot:
            print("📗 Стакан:")
            print(f"   Best Bid: {snapshot.best_bid:.2f}" if snapshot.best_bid else "   Best Bid: N/A")
            print(f"   Best Ask: {snapshot.best_ask:.2f}" if snapshot.best_ask else "   Best Ask: N/A")
            print(f"   Spread: {snapshot.spread:.2f}" if snapshot.spread else "   Spread: N/A")
            print(f"   Bid Volume: {snapshot.bid_volume}")
            print(f"   Ask Volume: {snapshot.ask_volume}")
            print(f"   Imbalance: {analysis.imbalance:+.3f}")
            
            # Визуализация imbalance
            bar_length = 20
            mid = bar_length // 2
            if analysis.imbalance > 0:
                filled = int(analysis.imbalance * mid)
                bar = " " * mid + "│" + "█" * filled + "░" * (mid - filled)
            else:
                filled = int(abs(analysis.imbalance) * mid)
                bar = "░" * (mid - filled) + "█" * filled + "│" + " " * mid
            print(f"   [{bar}]")
            print(f"   SELL ◄{'─'*16}► BUY")
        else:
            print("📗 Стакан: нет данных")
        
        print()
        
        # Сделки
        print("📊 Сделки:")
        print(f"   Volume (1s): {analyzer.trades.get_volume(figi, 1.0)}")
        print(f"   Volume (5s): {analyzer.trades.get_volume(figi, 5.0)}")
        print(f"   Buy/Sell (1s): {analyzer.trades.get_buy_volume(figi, 1.0)} / {analyzer.trades.get_sell_volume(figi, 1.0)}")
        print(f"   Volume Ratio: {analysis.volume_ratio:.2f}x")
        
        is_spike = analysis.volume_ratio >= config.validation.volume_spike_threshold
        print(f"   Volume Spike: {'🔥 YES' if is_spike else '❄️ NO'} (threshold: {config.validation.volume_spike_threshold}x)")
        
        print()
        
        # Итоговый сигнал
        print("🎯 СИГНАЛ:")
        
        sentiment_emoji = {
            Sentiment.BULLISH: "🟢 BULLISH (покупка)",
            Sentiment.BEARISH: "🔴 BEARISH (продажа)", 
            Sentiment.NEUTRAL: "⚪ NEUTRAL (нет сигнала)"
        }
        
        print(f"   Sentiment: {sentiment_emoji[analysis.detected_sentiment]}")
        print(f"   Confidence: {analysis.confidence:.1%}")
        print(f"   Validation: {analysis.validation_result.value.upper()}")
        
        print()
        print(f"   Детали: {analysis.details}")
        
        print()
        print("="*60)


async def monitor_orderflow(ticker: str, duration_sec: int = 30):
    """
    Мониторинг Order Flow в реальном времени.
    """
    print(f"\n🔴 LIVE MONITOR: {ticker} ({duration_sec}s)")
    print("Press Ctrl+C to stop\n")
    
    config = Config.from_env()
    figi = config.get_figi(ticker)
    
    if not figi:
        print(f"❌ Тикер {ticker} не найден")
        return
    
    with Client(config.tinkoff_token) as client:
        analyzer = FlowAnalyzer(client, config)
        
        start = datetime.now()
        
        try:
            while (datetime.now() - start).seconds < duration_sec:
                analysis = await analyzer.analyze(figi)
                
                # Компактный вывод
                sentiment_char = {
                    Sentiment.BULLISH: "▲",
                    Sentiment.BEARISH: "▼",
                    Sentiment.NEUTRAL: "─"
                }
                
                snapshot = analyzer.orderbook.get_latest(figi)
                price = snapshot.best_bid if snapshot else 0
                
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"{sentiment_char[analysis.detected_sentiment]} "
                    f"Price: {price:.2f} | "
                    f"Imb: {analysis.imbalance:+.2f} | "
                    f"Vol: {analysis.volume_ratio:.1f}x | "
                    f"Conf: {analysis.confidence:.0%}"
                )
                
                await asyncio.sleep(1)
                
        except KeyboardInterrupt:
            print("\n⏹ Stopped")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python test_orderflow.py SBER          # Один анализ")
        print("  python test_orderflow.py SBER --live   # Мониторинг 30 сек")
        sys.exit(1)
    
    ticker = sys.argv[1].upper()
    
    if "--live" in sys.argv:
        asyncio.run(monitor_orderflow(ticker))
    else:
        asyncio.run(test_orderflow(ticker))

