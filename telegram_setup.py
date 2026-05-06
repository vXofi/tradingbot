#!/usr/bin/env python3
"""
Настройка Telegram для мониторинга каналов.

ВАЖНО: Прочитайте перед запуском!

1. АККАУНТ:
   - Используйте аккаунт возрастом 3-6 месяцев
   - Реальный номер телефона (не VoIP!)
   - Включите 2FA в настройках Telegram
   - Присоединитесь к каналам ВРУЧНУЮ через телефон

2. API КЛЮЧИ:
   - Перейдите на https://my.telegram.org
   - Авторизуйтесь
   - Создайте новое приложение
   - Скопируйте API ID и API Hash

3. ДОБАВЬТЕ В .env:
   TELEGRAM_API_ID=your_id
   TELEGRAM_API_HASH=your_hash

4. ЗАПУСТИТЕ ЭТОТ СКРИПТ:
   python telegram_setup.py

   При первом запуске введите код из SMS.
   После этого создастся файл tradingbot.session — НЕ УДАЛЯЙТЕ ЕГО!

Использование:
    python telegram_setup.py              # Авторизация и тест
    python telegram_setup.py --channels   # Список доступных каналов
    python telegram_setup.py --listen     # Тестовое прослушивание
"""

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


async def setup_and_test():
    """Настройка и тест Telegram."""
    
    print("\n" + "="*60)
    print("📱 TELEGRAM SETUP")
    print("="*60)
    
    # Проверяем ключи
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    
    if not api_id or not api_hash:
        print("""
❌ API credentials not found!

Шаги:
1. Перейдите на https://my.telegram.org
2. Авторизуйтесь по номеру телефона
3. Создайте новое приложение (любое название)
4. Скопируйте API ID и API Hash
5. Добавьте в файл .env:

   TELEGRAM_API_ID=123456
   TELEGRAM_API_HASH=abcdef1234567890

6. Запустите этот скрипт снова
""")
        return
    
    print(f"✅ API ID: {api_id}")
    print(f"✅ API Hash: {api_hash[:8]}...")
    
    # Импортируем после проверки (чтобы не падать на import)
    try:
        from telethon import TelegramClient
        from telethon.errors import FloodWaitError, SessionPasswordNeededError
    except ImportError:
        print("\n❌ Telethon not installed!")
        print("   Run: pip install telethon")
        return
    
    # Создаём клиент
    session_path = Path("tradingbot")
    client = TelegramClient(
        str(session_path),
        int(api_id),
        api_hash,
        device_model="Desktop",
        system_version="Windows 10",
        app_version="1.0",
    )
    
    print("\n🔌 Connecting to Telegram...")
    
    try:
        await client.start()
    except SessionPasswordNeededError:
        print("\n🔐 2FA enabled! Enter your cloud password:")
        password = input("Password: ")
        await client.sign_in(password=password)
    except FloodWaitError as e:
        print(f"\n⚠️ FloodWait: wait {e.seconds} seconds before trying again")
        return
    
    # Информация об аккаунте
    me = await client.get_me()
    print(f"\n✅ Logged in successfully!")
    print(f"   Name: {me.first_name} {me.last_name or ''}")
    print(f"   Username: @{me.username or 'not set'}")
    print(f"   Phone: +{me.phone}")
    
    # Проверяем сессию
    session_file = Path("tradingbot.session")
    if session_file.exists():
        print(f"\n✅ Session saved: {session_file}")
        print("   (Don't delete this file! It stores your auth)")
    
    # Режим --channels
    if "--channels" in sys.argv:
        print("\n" + "="*60)
        print("📢 YOUR CHANNELS & GROUPS")
        print("="*60)
        
        async for dialog in client.iter_dialogs():
            if dialog.is_channel or dialog.is_group:
                entity = dialog.entity
                username = f"@{entity.username}" if entity.username else f"id:{entity.id}"
                print(f"  {dialog.title[:30]:30} | {username}")
        
        print("\n💡 Add channel usernames to monitor in your config")
    
    # Режим --listen
    elif "--listen" in sys.argv:
        print("\n" + "="*60)
        print("🎧 TEST LISTENING MODE")
        print("="*60)
        print("Listening for ALL new messages (30 seconds)...")
        print("Press Ctrl+C to stop early\n")
        
        @client.on(events.NewMessage())
        async def handler(event):
            chat = await event.get_chat()
            title = getattr(chat, 'title', 'Private')
            text = event.message.text or "[media]"
            preview = text[:50].replace('\n', ' ')
            print(f"[{datetime.now().strftime('%H:%M:%S')}] {title}: {preview}...")
        
        # Слушаем 30 секунд
        try:
            await asyncio.sleep(30)
        except KeyboardInterrupt:
            pass
        
        print("\n✅ Test complete!")
    
    else:
        print("""
✅ Setup complete!

Следующие шаги:

1. Посмотрите список каналов:
   python telegram_setup.py --channels

2. Протестируйте прослушивание:
   python telegram_setup.py --listen

3. Настройте каналы для мониторинга в вашем конфиге

4. Запустите бота с Telegram:
   python run_telegram.py
""")
    
    await client.disconnect()


# Нужен импорт events для --listen
try:
    from telethon import events
except ImportError:
    pass


if __name__ == "__main__":
    asyncio.run(setup_and_test())

