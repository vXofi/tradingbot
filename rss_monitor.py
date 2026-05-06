#!/usr/bin/env python3
"""
RSS Monitor — standalone script for testing RSS feed listener.

Usage:
    python rss_monitor.py                    # Continuous monitoring
    python rss_monitor.py --once             # Single poll, print results
    python rss_monitor.py --test             # Test feed connectivity
    python rss_monitor.py --parse            # Poll + parse through NewsParser
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from bot.listeners.rss_listener import RSSListener, FeedEntry
from bot.listeners.news_parser import NewsParser
from bot.models import Sentiment
from bot.utils import EventLogger


class RSSMonitor:
    """RSS feed monitoring with news parsing integration."""

    def __init__(self, use_parser: bool = True):
        self.logger = EventLogger(console_output=True)
        self.listener = RSSListener(
            feeds_config_path=Path("data/rss_feeds.json"),
            logger=self.logger,
        )
        self.parser = NewsParser() if use_parser else None

        self._entries_received = 0
        self._signals_detected = 0

    async def on_entry(self, entry: FeedEntry):
        """Callback for new RSS entries."""
        self._entries_received += 1

        print(f"\n{'─'*65}")
        print(f"📰 [{entry.feed_name}]")
        print(f"   {entry.title[:80]}{'...' if len(entry.title) > 80 else ''}")
        if entry.published:
            print(f"   Published: {entry.published.strftime('%H:%M:%S %d.%m.%Y')}")
        if entry.link:
            print(f"   Link: {entry.link[:70]}...")

        if self.parser:
            result = self.parser.parse(entry.full_text, source=f"rss:{entry.feed_name}")

            if result.success and result.tickers:
                self._signals_detected += 1

                emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪"}
                tickers_str = ", ".join(result.tickers)
                print(f"\n   ⚡ SIGNAL: {tickers_str} "
                      f"{emoji.get(result.sentiment.value, '')} {result.sentiment.value} "
                      f"({result.confidence:.0%}) [{result.method.value}] {result.signal_type}")

                if result.keywords_found:
                    print(f"   Keywords: {', '.join(result.keywords_found)}")

                self.logger.news_parsed(
                    ticker=tickers_str,
                    sentiment=result.sentiment.value,
                    confidence=result.confidence,
                    keywords=result.keywords_found,
                    method=result.method.value,
                )

        print(f"{'─'*65}")

    async def test_feeds(self):
        """Test connectivity to all configured feeds."""
        print("\n" + "=" * 65)
        print("RSS FEED CONNECTIVITY TEST")
        print("=" * 65)

        entries = await self.listener.poll_once()

        self.listener.print_stats()

        print(f"Total entries retrieved: {len(entries)}")
        if entries:
            print(f"\nSample entries (first 5):")
            for entry in entries[:5]:
                age = ""
                if entry.published:
                    age_sec = (datetime.now(entry.published.tzinfo) - entry.published).total_seconds()
                    age = f" ({int(age_sec // 60)}min ago)"
                print(f"  [{entry.feed_name}] {entry.title[:60]}{age}")

        print("=" * 65 + "\n")

    async def poll_once_with_parse(self):
        """Single poll + parse all entries."""
        print("\n" + "=" * 65)
        print("RSS POLL + PARSE")
        print("=" * 65)

        entries = await self.listener.poll_once()
        print(f"\nRetrieved {len(entries)} new entries\n")

        if not entries:
            print("No new entries found.")
            print("=" * 65 + "\n")
            return

        signals = []
        for entry in entries:
            if self.parser:
                result = self.parser.parse(entry.full_text, source=f"rss:{entry.feed_name}")
                if result.success and result.tickers and result.sentiment != Sentiment.NEUTRAL:
                    signals.append((entry, result))

        print(f"Signals detected: {len(signals)} / {len(entries)} entries\n")

        for entry, result in signals:
            emoji = {"bullish": "🟢", "bearish": "🔴"}
            tickers_str = ", ".join(result.tickers[:3])
            if len(result.tickers) > 3:
                tickers_str += f" +{len(result.tickers) - 3}"
            print(f"  {emoji.get(result.sentiment.value, '⚪')} {tickers_str:20} "
                  f"{result.sentiment.value:8} {result.confidence:.0%} "
                  f"[{result.signal_type}] | {entry.title[:45]}")

        self.listener.print_stats()

    async def run_continuous(self):
        """Continuous monitoring with parsing."""
        self.listener.on_entry(self.on_entry)

        print("\n" + "=" * 65)
        print("RSS MONITOR — Continuous Mode")
        print("=" * 65)

        if self.parser:
            print(f"Parser: {len(self.parser.bullish_keywords)} bullish, "
                  f"{len(self.parser.bearish_keywords)} bearish keywords")

        print("\nWaiting for new entries... (Ctrl+C to stop)\n")

        try:
            await self.listener.start()
        except KeyboardInterrupt:
            pass
        finally:
            self.listener.stop()

            print("\n" + "=" * 65)
            print("SESSION SUMMARY")
            print("=" * 65)
            print(f"Entries received: {self._entries_received}")
            print(f"Signals detected: {self._signals_detected}")
            self.listener.print_stats()


async def main():
    if "--test" in sys.argv:
        monitor = RSSMonitor(use_parser=False)
        await monitor.test_feeds()

    elif "--once" in sys.argv:
        monitor = RSSMonitor(use_parser=False)
        entries = await monitor.listener.poll_once()
        print(f"\nRetrieved {len(entries)} entries:\n")
        for entry in entries:
            print(f"  [{entry.feed_name}] {entry.title[:70]}")
        monitor.listener.print_stats()

    elif "--parse" in sys.argv:
        monitor = RSSMonitor(use_parser=True)
        await monitor.poll_once_with_parse()

    else:
        monitor = RSSMonitor(use_parser=True)
        await monitor.run_continuous()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bye!")
