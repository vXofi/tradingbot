"""
Модуль источников сигналов.

Компоненты:
- NewsParser: гибридный парсер (keywords + semantic)
- TelegramListener: мониторинг Telegram каналов
- RSSListener: мониторинг RSS лент финансовых новостей
"""

from .news_parser import NewsParser, ParseResult
from .telegram_listener import TelegramListener
from .rss_listener import RSSListener, FeedEntry

__all__ = [
    "NewsParser",
    "ParseResult",
    "TelegramListener",
    "RSSListener",
    "FeedEntry",
]
