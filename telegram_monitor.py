#!/usr/bin/env python3
"""
Telegram Monitor — мониторинг каналов для торгового бота.

⚠️ ПЕРЕД ЗАПУСКОМ:
1. Получите API_ID и API_HASH на https://my.telegram.org
2. Добавьте их в .env:
   TELEGRAM_API_ID=12345678
   TELEGRAM_API_HASH=abcdef1234567890
3. Присоединитесь к нужным каналам ВРУЧНУЮ через телефон
4. Используйте аккаунт старше 3+ месяцев с реальной активностью

Запуск:
    python telegram_monitor.py                  # Интерактивный режим
    python telegram_monitor.py --list           # Показать ваши каналы
    python telegram_monitor.py --channels @ch1 @ch2   # Мониторинг каналов
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from bot.listeners import TelegramListener, NewsParser
from bot.utils import EventLogger


class TelegramMonitor:
    """Интеграция Telegram с парсером новостей."""
    
    def __init__(self):
        # Проверяем API credentials
        self.api_id = os.getenv("TELEGRAM_API_ID")
        self.api_hash = os.getenv("TELEGRAM_API_HASH")
        
        if not self.api_id or not self.api_hash:
            print("❌ Missing Telegram credentials!")
            print()
            print("Add to your .env file:")
            print("  TELEGRAM_API_ID=your_api_id")
            print("  TELEGRAM_API_HASH=your_api_hash")
            print()
            print("Get them at: https://my.telegram.org")
            sys.exit(1)
        
        # Создаём компоненты
        self.logger = EventLogger(console_output=True)
        self.listener = TelegramListener(
            api_id=int(self.api_id),
            api_hash=self.api_hash,
            session_dir=Path("sessions"),
            logger=self.logger
        )
        self.parser = NewsParser()
        
        # Счётчики
        self._parsed_count = 0
        self._signals_count = 0
    
    async def on_message(self, message: dict):
        """Обработчик нового сообщения."""
        text = message["text"]
        channel = message["channel"]
        is_edit = message["is_edit"]
        
        if not text:
            return
        
        # Помечаем редакции
        edit_mark = " [EDIT]" if is_edit else ""
        
        print(f"\n{'─'*60}")
        print(f"📨 {channel}{edit_mark} | {message['timestamp'].strftime('%H:%M:%S')}")
        print(f"   {text[:200]}{'...' if len(text) > 200 else ''}")
        
        result = self.parser.parse(text, source=f"telegram:{channel}")
        self._parsed_count += 1
        
        if result.success and result.tickers:
            self._signals_count += 1
            
            sentiment_emoji = {
                "bullish": "🟢",
                "bearish": "🔴",
                "neutral": "⚪"
            }
            
            tickers_str = ", ".join(result.tickers)
            print(f"\n   ⚡ SIGNAL DETECTED:")
            print(f"      Tickers: {tickers_str}")
            print(f"      Sentiment: {sentiment_emoji.get(result.sentiment.value, '')} {result.sentiment.value}")
            print(f"      Confidence: {result.confidence:.0%}")
            print(f"      Keywords: {', '.join(result.keywords_found) if result.keywords_found else 'N/A'}")
            print(f"      Method: {result.method.value}")
            print(f"      Signal type: {result.signal_type}")
            
            self.logger.news_parsed(
                ticker=tickers_str,
                sentiment=result.sentiment.value,
                confidence=result.confidence,
                keywords=result.keywords_found,
                method=result.method.value
            )
        
        print(f"{'─'*60}")
    
    async def run_monitor(self, channels: list[str]):
        """Запуск мониторинга."""
        # Подключаемся
        if not await self.listener.connect():
            return
        
        # Добавляем каналы
        self.listener.add_channels(channels)
        
        # Устанавливаем callback
        self.listener.on_message(self.on_message)
        
        print("\n" + "="*60)
        print("🎧 TELEGRAM MONITOR STARTED")
        print("="*60)
        print(f"Channels: {', '.join(channels)}")
        print(f"Parser: {len(self.parser.bullish_keywords)} bullish, "
              f"{len(self.parser.bearish_keywords)} bearish keywords")
        print("="*60)
        print("\nWaiting for messages... (Ctrl+C to stop)\n")
        
        try:
            await self.listener.start_listening()
        except KeyboardInterrupt:
            pass
        finally:
            await self.listener.disconnect()
            
            print("\n" + "="*60)
            print("SESSION SUMMARY")
            print("="*60)
            print(f"Messages parsed: {self._parsed_count}")
            print(f"Signals detected: {self._signals_count}")
            print("="*60)
    
    async def list_channels(self):
        """Показать список каналов."""
        if not await self.listener.connect():
            return
        
        await self.listener.list_dialogs(limit=30)
        await self.listener.disconnect()
    
    async def interactive(self):
        """Интерактивный режим."""
        print("\n" + "="*60)
        print("TELEGRAM MONITOR — Interactive Mode")
        print("="*60)
        print()
        print("Commands:")
        print("  list              — Show your channels/chats")
        print("  monitor @ch1 @ch2 — Start monitoring channels")
        print("  quit              — Exit")
        print()
        
        # Подключаемся один раз
        if not await self.listener.connect():
            return
        
        try:
            while True:
                try:
                    cmd = input("\n> ").strip()
                except EOFError:
                    break
                
                if not cmd:
                    continue
                
                parts = cmd.split()
                action = parts[0].lower()
                
                if action in ("quit", "exit", "q"):
                    break
                
                elif action == "list":
                    await self.listener.list_dialogs()
                
                elif action == "monitor":
                    if len(parts) < 2:
                        print("Usage: monitor @channel1 @channel2 ...")
                        continue
                    
                    channels = parts[1:]
                    self.listener.add_channels(channels)
                    self.listener.on_message(self.on_message)
                    
                    print(f"\n🎧 Monitoring {len(channels)} channel(s)...")
                    print("Press Ctrl+C to stop and return to menu\n")
                    
                    try:
                        await self.listener.start_listening()
                    except KeyboardInterrupt:
                        print("\n⏸ Monitoring paused")
                
                else:
                    print(f"Unknown command: {action}")
                    
        finally:
            await self.listener.disconnect()


async def main():
    monitor = TelegramMonitor()
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "--list":
            await monitor.list_channels()
        
        elif sys.argv[1] == "--channels":
            channels = sys.argv[2:]
            if not channels:
                print("Usage: python telegram_monitor.py --channels @channel1 @channel2")
                sys.exit(1)
            await monitor.run_monitor(channels)
        
        else:
            print(__doc__)
    else:
        await monitor.interactive()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bye!")

