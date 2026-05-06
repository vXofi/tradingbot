#!/usr/bin/env python3
"""
CLI для тестирования Event-Driven Trading Bot.

Команды:
    python cli.py                     # Интерактивный режим
    python cli.py signal SBER bullish # Разовый сигнал
    python cli.py news "Сбер рекомендует дивиденды"  # Парсинг новости
    python cli.py analyze SBER        # Анализ Order Flow
    python cli.py status              # Статус бота
"""

import asyncio
import sys
from datetime import datetime

from bot.config import Config
from bot.core import TradingBot
from bot.models import Sentiment
from bot.listeners import NewsParser
from bot.utils import EventLogger


class CLI:
    """Интерактивный CLI для бота."""
    
    def __init__(self):
        self.bot: TradingBot = None
        self.news_parser: NewsParser = None
        self.running = False
    
    async def start(self):
        """Запуск CLI."""
        config = Config.from_env()
        self.bot = TradingBot(config)
        await self.bot.start()
        
        # Инициализируем парсер новостей
        self.news_parser = NewsParser(use_llm_fallback=True)
        print(f"📰 NewsParser: {len(self.news_parser.bullish_keywords)} bullish, "
              f"{len(self.news_parser.bearish_keywords)} bearish keywords")
        if self.news_parser.gemini_api_key:
            print("   Gemini API: ✅ configured")
        
        self.running = True
    
    async def stop(self):
        """Остановка CLI."""
        self.running = False
        if self.bot:
            await self.bot.stop()
    
    def print_help(self):
        """Вывод справки."""
        print("""
╔════════════════════════════════════════════════════════════╗
║              EVENT-DRIVEN TRADING BOT CLI                  ║
╠════════════════════════════════════════════════════════════╣
║  NEWS PARSING:                                             ║
║                                                            ║
║  news <text>                                               ║
║      Parse news text and extract signal (no trading)       ║
║      Example: news Сбер рекомендует дивиденды              ║
║                                                            ║
║  trade <text>                                              ║
║      Parse news AND execute trade if signal confirmed      ║
║      Example: trade Газпром отменил выплату дивидендов     ║
║                                                            ║
║  MANUAL SIGNALS:                                           ║
║                                                            ║
║  signal <TICKER> <bullish|bearish|neutral>                 ║
║      Send a manual trading signal                          ║
║      Example: signal SBER bullish                          ║
║                                                            ║
║  ORDER FLOW:                                               ║
║                                                            ║
║  analyze <TICKER>                                          ║
║      Analyze Order Flow without trading                    ║
║      Example: analyze GAZP                                 ║
║                                                            ║
║  POSITIONS:                                                ║
║                                                            ║
║  positions        - Show open positions                    ║
║  close <TICKER>   - Close position manually                ║
║  stats            - Show trading statistics                ║
║                                                            ║
║  OTHER:                                                    ║
║                                                            ║
║  whitelist        - Show allowed tickers                   ║
║  status           - Bot status                             ║
║  logs [N]         - Show last N events (default 10)        ║
║  help             - Show this help                         ║
║  quit / exit      - Exit the CLI                           ║
╚════════════════════════════════════════════════════════════╝
""")
    
    async def handle_command(self, line: str):
        """Обработка команды."""
        parts = line.strip().split()
        if not parts:
            return
        
        cmd = parts[0].lower()
        args = parts[1:]
        
        try:
            if cmd in ('help', 'h', '?'):
                self.print_help()
            
            elif cmd in ('quit', 'exit', 'q'):
                await self.stop()
            
            elif cmd == 'news':
                await self.cmd_news(args, execute=False)
            
            elif cmd == 'trade':
                await self.cmd_news(args, execute=True)
            
            elif cmd == 'signal':
                await self.cmd_signal(args)
            
            elif cmd == 'analyze':
                await self.cmd_analyze(args)
            
            elif cmd == 'positions':
                await self.cmd_positions()
            
            elif cmd == 'close':
                await self.cmd_close(args)
            
            elif cmd == 'stats':
                await self.cmd_stats()
            
            elif cmd == 'whitelist':
                await self.cmd_whitelist()
            
            elif cmd == 'status':
                await self.cmd_status()
            
            elif cmd == 'logs':
                await self.cmd_logs(args)
            
            else:
                print(f"Unknown command: {cmd}. Type 'help' for available commands.")
        
        except Exception as e:
            print(f"❌ Error: {e}")
    
    async def cmd_news(self, args: list, execute: bool = False):
        """Парсинг новости."""
        if not args:
            print("Usage: news <текст новости>")
            print("       trade <текст новости>  (parse + execute)")
            print("Example: news Сбер рекомендует дивиденды 33 рубля")
            return
        
        text = " ".join(args)
        
        print(f"\n📰 Parsing: {text[:60]}{'...' if len(text) > 60 else ''}")
        
        result = self.news_parser.parse(text, source="cli")
        
        if result.success and result.tickers:
            self.bot.logger.news_parsed(
                ticker=", ".join(result.tickers),
                sentiment=result.sentiment.value,
                confidence=result.confidence,
                keywords=result.keywords_found,
                method=result.method.value
            )
        elif not result.success:
            self.bot.logger.news_ignored(
                reason=result.reasoning or "parsing failed",
                text_preview=text[:100]
            )
        
        print(f"\n{'='*55}")
        print("PARSE RESULT")
        print(f"{'='*55}")
        
        status = "✅ SUCCESS" if result.success else "❌ FAILED"
        print(f"Status: {status}")
        
        if result.tickers:
            tickers_str = ", ".join(f"{t} ({self.news_parser.ticker_to_figi.get(t, '?')})" for t in result.tickers)
            print(f"Tickers: {tickers_str}")
        else:
            print("Ticker: ❌ not found")
        
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
        
        print(f"{'='*55}")
        
        if execute:
            if not result.success:
                print("\n⚠️ Cannot execute: parsing failed")
                return
            
            if not result.tickers:
                print("\n⚠️ Cannot execute: ticker not identified")
                return
            
            if result.sentiment == Sentiment.NEUTRAL:
                print("\n⚠️ Cannot execute: neutral sentiment")
                return
            
            if result.confidence < 0.5:
                print(f"\n⚠️ Cannot execute: low confidence ({result.confidence:.0%} < 50%)")
                return
            
            events = self.news_parser.to_news_events(result, source="cli")
            if events:
                print(f"\n🚀 Executing {len(events)} signal(s)...")
                for event in events:
                    print(f"   → {event.ticker} {event.sentiment.value}")
                    await self.bot.process_event(event)
        else:
            if result.success and result.tickers and result.sentiment != Sentiment.NEUTRAL:
                print(f"\n💡 To execute this signal, use: trade {text[:30]}...")
    
    async def cmd_signal(self, args: list):
        """Отправить ручной сигнал."""
        if len(args) < 2:
            print("Usage: signal <TICKER> <bullish|bearish|neutral> [keywords...]")
            print("Example: signal SBER bullish дивиденды")
            return
        
        ticker = args[0].upper()
        sentiment = args[1].lower()
        keywords = args[2:] if len(args) > 2 else []
        
        event = self.bot.create_manual_event(ticker, sentiment, keywords)
        if event:
            await self.bot.process_event(event)
    
    async def cmd_analyze(self, args: list):
        """Анализ Order Flow."""
        if len(args) < 1:
            print("Usage: analyze <TICKER>")
            return
        
        ticker = args[0].upper()
        figi = self.bot.config.get_figi(ticker)
        
        if not figi:
            print(f"❌ Unknown ticker: {ticker}")
            return
        
        print(f"\n🔍 Analyzing {ticker}...")
        
        analysis = await self.bot.flow_analyzer.analyze(figi)
        
        print(f"\n{'='*50}")
        print(f"ORDER FLOW ANALYSIS: {ticker}")
        print(f"{'='*50}")
        print(f"Time: {analysis.timestamp.strftime('%H:%M:%S')}")
        print(f"\nMetrics:")
        print(f"  Imbalance:     {analysis.imbalance:+.3f}")
        print(f"  Volume ratio:  {analysis.volume_ratio:.2f}x")
        print(f"  Price change:  {analysis.price_change_percent:+.2f}%")
        print(f"\nSignal:")
        
        emoji = {
            Sentiment.BULLISH: "🟢",
            Sentiment.BEARISH: "🔴",
            Sentiment.NEUTRAL: "⚪"
        }
        
        print(f"  Sentiment:   {emoji[analysis.detected_sentiment]} {analysis.detected_sentiment.value}")
        print(f"  Confidence:  {analysis.confidence:.1%}")
        print(f"  Validation:  {analysis.validation_result.value}")
        print(f"\nDetails: {analysis.details}")
        print(f"{'='*50}\n")
    
    async def cmd_positions(self):
        """Показать позиции."""
        self.bot.position_tracker.print_status()
    
    async def cmd_close(self, args: list):
        """Закрыть позицию."""
        if len(args) < 1:
            print("Usage: close <TICKER>")
            return
        
        ticker = args[0].upper()
        figi = self.bot.config.get_figi(ticker)
        
        if not figi:
            print(f"❌ Unknown ticker: {ticker}")
            return
        
        if not self.bot.position_tracker.has_position(figi):
            print(f"⚠️ No open position for {ticker}")
            return
        
        print(f"Closing position {ticker}...")
        await self.bot.order_manager.close_position_market(figi, "MANUAL")
    
    async def cmd_stats(self):
        """Показать статистику."""
        print("\n" + "="*50)
        print("TRADING STATISTICS")
        print("="*50)
        
        stats = self.bot.position_tracker.get_stats()
        daily = self.bot.risk_manager.get_daily_stats()
        
        print(f"\nSession:")
        print(f"  Events received:   {self.bot._events_received}")
        print(f"  Events validated:  {self.bot._events_validated}")
        print(f"  Trades executed:   {self.bot._trades_executed}")
        
        print(f"\nAll time:")
        print(f"  Total trades: {stats['total_trades']}")
        print(f"  Win rate:     {stats['win_rate']:.1%}")
        print(f"  Total PnL:    {stats['total_pnl']:+.2f} RUB")
        print(f"  Best trade:   {stats['best_trade']:+.2f} RUB")
        print(f"  Worst trade:  {stats['worst_trade']:+.2f} RUB")
        
        print(f"\nToday:")
        print(f"  Daily PnL:    {daily['daily_pnl']:+.2f} RUB")
        print(f"  Daily trades: {daily['daily_trades']}")
        print(f"  Loss limit remaining: {daily['remaining_loss_limit']:.2f} RUB")
        
        print("="*50 + "\n")
    
    async def cmd_whitelist(self):
        """Показать whitelist."""
        print("\n" + "="*50)
        print("WHITELIST")
        print("="*50)
        
        for ticker, figi in sorted(self.bot.config.whitelist.items()):
            print(f"  {ticker:6} -> {figi}")
        
        print(f"\nTotal: {len(self.bot.config.whitelist)} instruments")
        print("="*50 + "\n")
    
    async def cmd_status(self):
        """Показать статус."""
        status = self.bot.get_status()
        
        print("\n" + "="*50)
        print("BOT STATUS")
        print("="*50)
        print(f"  Running:    {'✅ Yes' if status['running'] else '❌ No'}")
        print(f"  Mode:       {'📦 Sandbox' if status['sandbox'] else '💰 Production'}")
        print(f"  Account:    {status['account_id']}")
        print(f"  Positions:  {status['positions']}")
        print("="*50 + "\n")
    
    async def cmd_logs(self, args: list):
        """Показать последние события из лога."""
        limit = 10
        if args:
            try:
                limit = int(args[0])
            except ValueError:
                pass
        
        events = self.bot.logger.get_today_events()
        
        print("\n" + "="*60)
        print(f"EVENT LOG (last {limit} of {len(events)} today)")
        print("="*60)
        
        if not events:
            print("No events logged today")
        else:
            # Показываем последние N
            for event in events[-limit:]:
                time_str = event.get('timestamp', '').split('T')[1].split('.')[0]
                event_type = event.get('event_type', 'unknown')
                ticker = event.get('ticker', '')
                sentiment = event.get('sentiment', '')
                confidence = event.get('confidence')
                pnl = event.get('pnl')
                reason = event.get('reason', '')
                
                emoji = {
                    'news_parsed': '📰',
                    'news_ignored': '🚫',
                    'signal_validated': '✅',
                    'signal_rejected': '❌',
                    'signal_inconclusive': '⚪',
                    'position_opened': '📈',
                    'position_closed': '📉',
                    'stop_loss_hit': '🛑',
                    'take_profit_hit': '🎯',
                    'error': '💥',
                }.get(event_type, '📝')
                
                parts = [f"[{time_str}]", emoji, event_type]
                if ticker:
                    parts.append(f"[{ticker}]")
                if sentiment:
                    sent_emoji = {'bullish': '🟢', 'bearish': '🔴', 'neutral': '⚪'}.get(sentiment, '')
                    parts.append(f"{sent_emoji}{sentiment}")
                if confidence is not None:
                    parts.append(f"{confidence:.0%}")
                if pnl is not None:
                    parts.append(f"PnL:{pnl:+.2f}")
                if reason:
                    parts.append(f"({reason[:30]})")
                
                print(" ".join(parts))
        
        # Статистика
        log_stats = self.bot.logger.get_stats()
        print(f"\nTotal events today: {log_stats['total_events']}")
        if log_stats['event_counts']:
            print("By type:", dict(log_stats['event_counts']))
        print("="*60 + "\n")
    
    async def interactive_loop(self):
        """Интерактивный цикл."""
        self.print_help()
        
        while self.running:
            try:
                line = input("\n> ").strip()
                if line:
                    await self.handle_command(line)
            except KeyboardInterrupt:
                print("\n")
                await self.stop()
            except EOFError:
                await self.stop()


async def main_interactive():
    """Запуск интерактивного режима."""
    cli = CLI()
    await cli.start()
    await cli.interactive_loop()


async def main_command(args: list):
    """Запуск одной команды."""
    cli = CLI()
    await cli.start()
    
    cmd = " ".join(args)
    await cli.handle_command(cmd)
    
    await cli.stop()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Режим одной команды
        asyncio.run(main_command(sys.argv[1:]))
    else:
        # Интерактивный режим
        asyncio.run(main_interactive())

