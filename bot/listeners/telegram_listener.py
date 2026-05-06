"""
Telegram Listener — безопасный мониторинг каналов через Telethon (MTProto).

ВАЖНЫЕ ПРАВИЛА ДЛЯ ИЗБЕЖАНИЯ БАНА:
1. Используем Events (не polling)
2. Обрабатываем FloodWait
3. Session persistence (не логинимся каждый раз)
4. Только пассивный мониторинг (слушаем, не скрапим историю)
5. Каналы присоединять вручную через телефон

Требования:
- API_ID и API_HASH с my.telegram.org (свои, не чужие!)
- Аккаунт старше 3-6 месяцев с реальной активностью
- Реальный номер телефона (не VoIP)
- 2FA включена
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Awaitable

from telethon import TelegramClient, events
from telethon.errors import (
    FloodWaitError,
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
)
from telethon.tl.types import Channel, Chat, User

from ..models import Sentiment
from ..utils import EventLogger, EventType


class TelegramListener:
    """
    Безопасный Telegram listener.
    
    Использует event-driven подход (не polling).
    Автоматически обрабатывает FloodWait.
    Session сохраняется для повторного использования.
    """
    
    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session_name: str = "tradingbot",
        session_dir: Path = Path("sessions"),
        logger: Optional[EventLogger] = None
    ):
        """
        Args:
            api_id: Telegram API ID (с my.telegram.org)
            api_hash: Telegram API Hash
            session_name: Имя файла сессии (без .session)
            session_dir: Директория для хранения сессий
            logger: EventLogger для логирования
        """
        self.api_id = api_id
        self.api_hash = api_hash
        self.logger = logger
        
        # Создаём директорию для сессий
        session_dir.mkdir(exist_ok=True)
        session_path = session_dir / session_name
        
        # Создаём клиент с сохранением сессии
        self.client = TelegramClient(
            str(session_path),
            api_id,
            api_hash,
            device_model="Desktop",
            system_version="Windows 10",
            app_version="1.0",
            lang_code="ru",
            system_lang_code="ru"
        )
        
        # Каналы для мониторинга (добавлять через add_channel)
        self._channels: list[str] = []
        
        # Callback для обработки сообщений
        self._message_callback: Optional[Callable[[dict], Awaitable[None]]] = None
        
        # Статистика
        self._messages_received = 0
        self._start_time: Optional[datetime] = None
        
        # Флаг работы
        self._running = False
    
    async def connect(self):
        """
        Подключение к Telegram.
        
        При первом запуске попросит ввести номер телефона и код.
        Сессия сохраняется — повторный вход не нужен.
        """
        print("\n📱 Connecting to Telegram...")
        
        try:
            await self.client.connect()
            
            if not await self.client.is_user_authorized():
                print("\n⚠️  First-time login required.")
                print("   Your session will be saved for future runs.")
                print()
                
                phone = input("📞 Enter phone number (with country code, e.g. +7...): ").strip()
                
                try:
                    await self.client.send_code_request(phone)
                    code = input("🔢 Enter the code from Telegram: ").strip()
                    
                    try:
                        await self.client.sign_in(phone, code)
                    except SessionPasswordNeededError:
                        # 2FA включена
                        password = input("🔐 Enter your 2FA password: ").strip()
                        await self.client.sign_in(password=password)
                        
                except PhoneCodeInvalidError:
                    print("❌ Invalid code. Please restart and try again.")
                    return False
                except PhoneNumberBannedError:
                    print("❌ This phone number is banned by Telegram.")
                    return False
            
            me = await self.client.get_me()
            print(f"✅ Connected as: {me.first_name} (@{me.username or 'no username'})")
            
            if self.logger:
                self.logger.log(
                    EventType.BOT_STARTED,
                    reason="Telegram connected",
                    details={"username": me.username}
                )
            
            return True
            
        except FloodWaitError as e:
            print(f"⏳ FloodWait: need to wait {e.seconds} seconds")
            print("   Telegram is rate-limiting. Try again later.")
            return False
        except Exception as e:
            print(f"❌ Connection error: {e}")
            return False
    
    async def disconnect(self):
        """Отключение от Telegram."""
        self._running = False
        await self.client.disconnect()
        print("📴 Disconnected from Telegram")
    
    def add_channel(self, channel: str):
        """
        Добавить канал для мониторинга.
        
        Args:
            channel: Username канала (@channelname) или ID
        
        Note:
            Канал должен быть уже присоединён к аккаунту вручную!
            Не добавляйте каналы программно — это повышает риск бана.
        """
        if channel not in self._channels:
            self._channels.append(channel)
            print(f"📢 Added channel: {channel}")
    
    def add_channels(self, channels: list[str]):
        """Добавить несколько каналов."""
        for ch in channels:
            self.add_channel(ch)
    
    def on_message(self, callback: Callable[[dict], Awaitable[None]]):
        """
        Установить callback для обработки сообщений.
        
        Callback получает dict:
        {
            "text": str,
            "channel": str,
            "channel_id": int,
            "message_id": int,
            "timestamp": datetime,
            "is_edit": bool
        }
        """
        self._message_callback = callback
    
    async def _handle_new_message(self, event):
        """Обработчик нового сообщения (internal)."""
        try:
            # Получаем информацию о чате
            chat = await event.get_chat()
            
            # Фильтруем только каналы из нашего списка
            chat_username = getattr(chat, 'username', None)
            chat_id = chat.id
            
            # Проверяем, что это наш канал
            is_our_channel = False
            for ch in self._channels:
                if ch.startswith('@'):
                    if chat_username and ch[1:].lower() == chat_username.lower():
                        is_our_channel = True
                        break
                else:
                    try:
                        if int(ch) == chat_id:
                            is_our_channel = True
                            break
                    except ValueError:
                        if ch.lower() == (chat_username or '').lower():
                            is_our_channel = True
                            break
            
            if not is_our_channel:
                return
            
            # Собираем данные сообщения
            message_data = {
                "text": event.message.text or "",
                "channel": f"@{chat_username}" if chat_username else str(chat_id),
                "channel_id": chat_id,
                "message_id": event.message.id,
                "timestamp": event.message.date,
                "is_edit": False
            }
            
            self._messages_received += 1
            
            # Логируем
            if self.logger:
                self.logger.log(
                    EventType.NEWS_PARSED,
                    reason="telegram_message",
                    details={
                        "channel": message_data["channel"],
                        "text_preview": message_data["text"][:100]
                    }
                )
            
            # Вызываем callback
            if self._message_callback:
                try:
                    await self._message_callback(message_data)
                except Exception as e:
                    print(f"⚠️ Message callback error: {e}")
                    if self.logger:
                        self.logger.error(f"Callback error: {e}")
                        
        except FloodWaitError as e:
            # ВАЖНО: Уважаем FloodWait!
            print(f"⏳ FloodWait in handler: sleeping {e.seconds}s")
            await asyncio.sleep(e.seconds)
        except Exception as e:
            print(f"⚠️ Message handler error: {e}")
    
    async def _handle_edited_message(self, event):
        """Обработчик отредактированного сообщения."""
        # Редакции важны — новость могла измениться
        try:
            chat = await event.get_chat()
            chat_username = getattr(chat, 'username', None)
            
            message_data = {
                "text": event.message.text or "",
                "channel": f"@{chat_username}" if chat_username else str(chat.id),
                "channel_id": chat.id,
                "message_id": event.message.id,
                "timestamp": event.message.edit_date or event.message.date,
                "is_edit": True
            }
            
            if self._message_callback:
                await self._message_callback(message_data)
                
        except Exception as e:
            print(f"⚠️ Edit handler error: {e}")
    
    async def start_listening(self):
        """
        Начать прослушивание каналов.
        
        Использует event-driven подход (НЕ polling).
        Работает до вызова stop() или Ctrl+C.
        """
        if not self._channels:
            print("⚠️ No channels to monitor. Use add_channel() first.")
            return
        
        print(f"\n🎧 Starting to listen to {len(self._channels)} channel(s):")
        for ch in self._channels:
            print(f"   • {ch}")
        print()
        print("Press Ctrl+C to stop\n")
        
        # Регистрируем обработчики событий
        # ВАЖНО: Используем events, НЕ polling!
        @self.client.on(events.NewMessage())
        async def new_message_handler(event):
            await self._handle_new_message(event)
        
        @self.client.on(events.MessageEdited())
        async def edited_message_handler(event):
            await self._handle_edited_message(event)
        
        self._running = True
        self._start_time = datetime.now()
        
        try:
            # Запускаем клиент и ждём событий
            await self.client.run_until_disconnected()
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            print(f"\n📊 Session stats: {self._messages_received} messages received")
    
    async def list_dialogs(self, limit: int = 20):
        """
        Показать список диалогов/каналов аккаунта.
        
        Полезно для определения ID каналов.
        
        ⚠️ Не вызывайте слишком часто — это активное действие!
        """
        print(f"\n📋 Your dialogs (last {limit}):\n")
        
        try:
            async for dialog in self.client.iter_dialogs(limit=limit):
                entity = dialog.entity
                
                if isinstance(entity, Channel):
                    type_str = "📢 Channel"
                elif isinstance(entity, Chat):
                    type_str = "👥 Group"
                elif isinstance(entity, User):
                    type_str = "👤 User"
                else:
                    type_str = "❓ Unknown"
                
                username = getattr(entity, 'username', None)
                title = getattr(entity, 'title', None) or getattr(entity, 'first_name', 'Unknown')
                
                print(f"{type_str} | {title[:30]:30} | @{username or 'N/A':20} | ID: {entity.id}")
                
        except FloodWaitError as e:
            print(f"⏳ FloodWait: need to wait {e.seconds} seconds")
            print("   Try again later.")
    
    def get_stats(self) -> dict:
        """Получить статистику."""
        uptime = None
        if self._start_time:
            uptime = (datetime.now() - self._start_time).total_seconds()
        
        return {
            "running": self._running,
            "channels": len(self._channels),
            "messages_received": self._messages_received,
            "uptime_seconds": uptime
        }


# Рекомендуемые каналы для финансовых новостей РФ
RECOMMENDED_CHANNELS = {
    "official": [
        # Официальные каналы компаний (самые надёжные)
        # Присоединяйтесь к ним ВРУЧНУЮ через телефон!
    ],
    "news": [
        # Новостные каналы (проверяйте достоверность)
        # Присоединяйтесь к ним ВРУЧНУЮ через телефон!
    ],
    "note": """
    ⚠️ ВАЖНО:
    1. Присоединяйтесь к каналам ВРУЧНУЮ через телефон
    2. Не добавляйте более 10-20 каналов сразу
    3. Используйте только проверенные источники
    4. Официальные каналы компаний надёжнее агрегаторов
    """
}
