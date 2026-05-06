#!/usr/bin/env python3
"""
Запуск бота с мониторингом Telegram каналов.

Использование:
    python run_telegram.py                    # Интерактивный режим
    python run_telegram.py --auto-trade       # Автоматическая торговля (осторожно!)

Перед запуском:
    1. Выполните python telegram_setup.py
    2. Убедитесь, что есть файл tradingbot.session
    3. Настройте список каналов ниже
"""

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# НАСТРОЙКА КАНАЛОВ
# =============================================================================

# Добавьте сюда каналы для мониторинга
# Используйте @username или ID канала
CHANNELS_TO_MONITOR = [
    # Официальные каналы компаний (безопасно, мало спама)
    # "@sberbank",        # Сбербанк официальный
    # "@gazprom_news",    # Газпром
    
    # Новостные каналы (больше сообщений)
    # "@markettwits",     # MarketTwits
    # "@smartlabnews",    # Смартлаб
    
    # Добавьте свои каналы:
    # "@your_channel",
]

# =============================================================================


async def main():
    """Главная функция."""
    
    # Проверяем настройку
    if not os.getenv("TELEGRAM_API_ID") or not os.getenv("TELEGRAM_API_HASH"):
        print("❌ Telegram API not configured!")
        print("   Run: python telegram_setup.py")
        return
    
    if not CHANNELS_TO_MONITOR:
        print("⚠️ No channels configured!")
        print("   Edit CHANNELS_TO_MONITOR in run_telegram.py")
        print("   Or run: python telegram_setup.py --channels")
        print()
        # Продолжаем без каналов (будет слушать всё)
    
    # Импортируем модули бота
    from bot.config import Config
    from bot.core import TradingBot
    from bot.listeners import (
        NewsParser,
        TelegramListener,
        TelegramConfig,
        TelegramNewsIntegration,
    )
    
    # Конфиг
    config = Config.from_env()
    auto_trade = "--auto-trade" in sys.argv
    
    if auto_trade:
        print("\n⚠️  AUTO-TRADE MODE ENABLED!")
        print("    Bot will automatically execute trades based on Telegram signals.")
        print("    Press Ctrl+C within 5 seconds to cancel...")
        try:
            await asyncio.sleep(5)
        except KeyboardInterrupt:
            print("\n❌ Cancelled")
            return
    
    # Инициализируем компоненты
    print("\n" + "="*60)
    print("🚀 STARTING TELEGRAM TRADING BOT")
    print("="*60)
    
    # 1. Trading Bot
    bot = TradingBot(config)
    await bot.start()
    
    # 2. News Parser
    parser = NewsParser(use_llm_fallback=True)
    print(f"📰 NewsParser ready ({len(parser.bullish_keywords)} keywords)")
    
    # 3. Telegram Listener
    tg_config = TelegramConfig()
    listener = TelegramListener(
        config=tg_config,
        channels=CHANNELS_TO_MONITOR,
        logger=bot.logger,
    )
    
    # 4. Интеграция
    integration = TelegramNewsIntegration(
        listener=listener,
        news_parser=parser,
        trading_bot=bot,
        auto_trade=auto_trade,
    )
    
    print(f"\n{'='*60}")
    print(f"Mode: {'🔴 AUTO-TRADE' if auto_trade else '👁️ MONITOR ONLY'}")
    print(f"Channels: {len(CHANNELS_TO_MONITOR)}")
    print(f"{'='*60}\n")
    
    # Запускаем
    try:
        await listener.run_forever()
    except KeyboardInterrupt:
        print("\n\n⏹️ Stopping...")
    finally:
        await listener.stop()
        await bot.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bye!")

