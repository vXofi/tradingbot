"""
RSS Feed Listener — async polling of financial news feeds.

Architecture:
- aiohttp for async HTTP (parallel fetching of multiple feeds)
- feedparser for RSS/Atom parsing
- Deduplication via entry ID/link hashing
- Freshness filtering (only recent entries)
- Per-feed configurable poll intervals
- Exponential backoff on errors
"""

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Optional, Awaitable

import aiohttp
import feedparser


@dataclass
class FeedConfig:
    """Configuration for a single RSS feed."""
    name: str
    url: str
    priority: str = "normal"
    poll_interval_sec: int = 30
    enabled: bool = True


@dataclass
class FeedEntry:
    """Parsed RSS entry."""
    feed_name: str
    title: str
    summary: str
    link: str
    published: Optional[datetime]
    entry_id: str
    full_text: str


@dataclass
class FeedStats:
    """Per-feed statistics."""
    last_poll: Optional[float] = None
    last_success: Optional[float] = None
    entries_total: int = 0
    entries_new: int = 0
    errors: int = 0
    consecutive_errors: int = 0


class RSSListener:
    """
    Async RSS feed listener.
    
    Polls multiple feeds concurrently at configurable intervals,
    deduplicates entries, and fires a callback for new items.
    """
    
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
    
    def __init__(
        self,
        feeds_config_path: Path = Path("data/rss_feeds.json"),
        max_entry_age_min: Optional[int] = None,
        request_timeout_sec: Optional[int] = None,
        max_retries: Optional[int] = None,
        backoff_base_sec: Optional[float] = None,
        logger=None,
    ):
        self.max_entry_age_min = 10
        self.request_timeout_sec = 15
        self.max_retries = 3
        self.backoff_base_sec = 5.0
        self.logger = logger
        
        self.feeds: list[FeedConfig] = []
        self._seen: dict[str, None] = {}  # OrderedDict-style (insertion order) with cap
        self._seen_max = 50_000
        self._stats: dict[str, FeedStats] = {}
        self._callback: Optional[Callable[[FeedEntry], Awaitable]] = None
        self._running = False
        self._tasks: list[asyncio.Task] = []
        
        self._load_config(feeds_config_path)
        
        # Explicit constructor args override config file defaults
        if max_entry_age_min is not None:
            self.max_entry_age_min = max_entry_age_min
        if request_timeout_sec is not None:
            self.request_timeout_sec = request_timeout_sec
        if max_retries is not None:
            self.max_retries = max_retries
        if backoff_base_sec is not None:
            self.backoff_base_sec = backoff_base_sec
    
    def _load_config(self, path: Path):
        """Load feed list from JSON config."""
        if not path.exists():
            print(f"  RSS config not found: {path}")
            return
        
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        defaults = data.get("defaults", {})
        self.max_entry_age_min = defaults.get("max_entry_age_min", self.max_entry_age_min)
        self.request_timeout_sec = defaults.get("request_timeout_sec", self.request_timeout_sec)
        self.max_retries = defaults.get("max_retries", self.max_retries)
        self.backoff_base_sec = defaults.get("backoff_base_sec", self.backoff_base_sec)
        
        for item in data.get("feeds", []):
            feed = FeedConfig(
                name=item["name"],
                url=item["url"],
                priority=item.get("priority", "normal"),
                poll_interval_sec=item.get("poll_interval_sec", 30),
                enabled=item.get("enabled", True),
            )
            self.feeds.append(feed)
            self._stats[feed.url] = FeedStats()
        
        enabled = [f for f in self.feeds if f.enabled]
        print(f"  RSS feeds loaded: {len(enabled)} enabled / {len(self.feeds)} total")
    
    def add_feed(self, name: str, url: str, poll_interval_sec: int = 30, priority: str = "normal"):
        """Add a feed at runtime."""
        feed = FeedConfig(name=name, url=url, priority=priority, poll_interval_sec=poll_interval_sec)
        self.feeds.append(feed)
        self._stats[url] = FeedStats()
    
    def on_entry(self, callback: Callable[[FeedEntry], Awaitable]):
        """Register callback for new entries. Callback receives FeedEntry."""
        self._callback = callback
    
    def _entry_hash(self, entry_id: str, link: str, title: str) -> str:
        """Generate dedup hash from entry identifiers."""
        raw = f"{entry_id}|{link}|{title}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _mark_seen(self, h: str):
        """Record hash in the bounded dedup cache."""
        self._seen[h] = None
        if len(self._seen) > self._seen_max:
            # Drop oldest 25 % to amortize the cleanup cost
            to_drop = self._seen_max // 4
            keys = list(self._seen.keys())[:to_drop]
            for k in keys:
                del self._seen[k]
    
    def _parse_published(self, entry) -> Optional[datetime]:
        """Extract publication datetime from an RSS entry."""
        for attr in ("published_parsed", "updated_parsed"):
            parsed = getattr(entry, attr, None)
            if parsed:
                try:
                    from calendar import timegm
                    ts = timegm(parsed)
                    return datetime.fromtimestamp(ts, tz=timezone.utc)
                except Exception:
                    continue
        
        for attr in ("published", "updated"):
            raw = getattr(entry, attr, None)
            if raw:
                try:
                    return parsedate_to_datetime(raw)
                except Exception:
                    continue
        
        return None
    
    def _is_fresh(self, published: Optional[datetime]) -> bool:
        """Check if entry is within the freshness window."""
        if published is None:
            return True  # assume fresh if no date
        
        now = datetime.now(timezone.utc)
        age_seconds = (now - published).total_seconds()
        return age_seconds <= self.max_entry_age_min * 60
    
    def _extract_text(self, entry) -> tuple[str, str]:
        """Extract title and summary text from entry, stripping HTML."""
        title = getattr(entry, "title", "") or ""
        
        summary = ""
        if hasattr(entry, "summary"):
            summary = entry.summary or ""
        elif hasattr(entry, "description"):
            summary = entry.description or ""
        
        import re
        summary = re.sub(r"<[^>]+>", " ", summary)
        summary = re.sub(r"\s+", " ", summary).strip()
        
        return title, summary
    
    async def _fetch_feed(self, session: aiohttp.ClientSession, feed: FeedConfig) -> list[FeedEntry]:
        """Fetch and parse a single RSS feed."""
        stats = self._stats[feed.url]
        stats.last_poll = time.time()
        
        try:
            timeout = aiohttp.ClientTimeout(total=self.request_timeout_sec)
            async with session.get(
                feed.url,
                timeout=timeout,
                headers={"User-Agent": self.USER_AGENT},
            ) as resp:
                if resp.status != 200:
                    stats.errors += 1
                    stats.consecutive_errors += 1
                    return []
                
                raw = await resp.text()
        except Exception as e:
            stats.errors += 1
            stats.consecutive_errors += 1
            if stats.consecutive_errors <= 2:
                err_msg = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                print(f"  RSS fetch error [{feed.name}]: {err_msg}")
            return []
        
        parsed = feedparser.parse(raw)
        
        if parsed.bozo and not parsed.entries:
            stats.errors += 1
            stats.consecutive_errors += 1
            return []
        
        stats.last_success = time.time()
        stats.consecutive_errors = 0
        
        new_entries = []
        for entry in parsed.entries:
            entry_id = getattr(entry, "id", "") or getattr(entry, "link", "") or ""
            link = getattr(entry, "link", "") or ""
            title, summary = self._extract_text(entry)
            
            if not title and not summary:
                continue
            
            h = self._entry_hash(entry_id, link, title)
            if h in self._seen:
                continue
            
            published = self._parse_published(entry)
            if not self._is_fresh(published):
                self._mark_seen(h)
                continue
            
            self._mark_seen(h)
            stats.entries_total += 1
            stats.entries_new += 1
            
            full_text = f"{title}. {summary}" if summary else title
            
            new_entries.append(FeedEntry(
                feed_name=feed.name,
                title=title,
                summary=summary,
                link=link,
                published=published,
                entry_id=h,
                full_text=full_text,
            ))
        
        return new_entries
    
    async def _poll_loop(self, feed: FeedConfig, session: aiohttp.ClientSession):
        """Polling loop for a single feed."""
        stats = self._stats[feed.url]
        
        while self._running:
            entries = await self._fetch_feed(session, feed)
            
            for entry in entries:
                if self._callback:
                    try:
                        await self._callback(entry)
                    except Exception as e:
                        print(f"  RSS callback error: {e}")
            
            # Backoff on consecutive errors
            if stats.consecutive_errors > 0:
                delay = min(
                    feed.poll_interval_sec * (2 ** stats.consecutive_errors),
                    300,  # cap at 5 minutes
                )
            else:
                delay = feed.poll_interval_sec
            
            await asyncio.sleep(delay)
    
    async def poll_once(self) -> list[FeedEntry]:
        """Single poll of all enabled feeds. Returns new entries."""
        enabled = [f for f in self.feeds if f.enabled]
        if not enabled:
            return []
        
        all_entries = []
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            tasks = [self._fetch_feed(session, feed) for feed in enabled]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for result in results:
                if isinstance(result, list):
                    all_entries.extend(result)
        
        return all_entries
    
    async def start(self):
        """Start continuous polling of all enabled feeds."""
        enabled = [f for f in self.feeds if f.enabled]
        if not enabled:
            print("  No RSS feeds enabled")
            return
        
        self._running = True
        
        print(f"\n  Starting RSS polling for {len(enabled)} feeds:")
        for f in enabled:
            print(f"    [{f.priority:>6}] {f.name} (every {f.poll_interval_sec}s)")
        print()
        
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
            # Initial poll — seed the dedup set without firing callbacks
            await self._initial_poll(session, enabled)
            
            # Start per-feed polling loops
            self._tasks = [
                asyncio.create_task(self._poll_loop(feed, session))
                for feed in enabled
            ]
            
            try:
                await asyncio.gather(*self._tasks)
            except asyncio.CancelledError:
                pass
    
    async def _initial_poll(self, session: aiohttp.ClientSession, feeds: list[FeedConfig]):
        """First poll to seed dedup set — doesn't fire callbacks."""
        saved_callback = self._callback
        self._callback = None
        
        tasks = [self._fetch_feed(session, feed) for feed in feeds]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        total_seeded = sum(len(r) for r in results if isinstance(r, list))
        print(f"  Initial poll: {total_seeded} existing entries indexed (dedup seeded)")
        
        # Reset new-entry counters after seeding
        for stats in self._stats.values():
            stats.entries_new = 0
        
        self._callback = saved_callback
    
    def stop(self):
        """Stop all polling loops."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
    
    def get_stats(self) -> dict:
        """Get per-feed statistics."""
        result = {}
        for feed in self.feeds:
            s = self._stats.get(feed.url, FeedStats())
            result[feed.name] = {
                "enabled": feed.enabled,
                "priority": feed.priority,
                "poll_interval": feed.poll_interval_sec,
                "entries_total": s.entries_total,
                "entries_new": s.entries_new,
                "errors": s.errors,
                "consecutive_errors": s.consecutive_errors,
                "last_poll": datetime.fromtimestamp(s.last_poll).strftime("%H:%M:%S") if s.last_poll else "never",
                "last_success": datetime.fromtimestamp(s.last_success).strftime("%H:%M:%S") if s.last_success else "never",
            }
        return result
    
    def get_seen_count(self) -> int:
        """Number of entries in the dedup set."""
        return len(self._seen)
    
    def clear_seen(self):
        """Clear dedup cache (useful after long runs to free memory)."""
        self._seen.clear()
    
    def print_stats(self):
        """Pretty-print feed stats."""
        stats = self.get_stats()
        
        print(f"\n{'='*65}")
        print("RSS FEED STATISTICS")
        print(f"{'='*65}")
        print(f"{'Feed':<35} {'New':>5} {'Total':>6} {'Err':>4} {'Last OK':>8}")
        print(f"{'-'*65}")
        
        for name, s in stats.items():
            status = "" if s["enabled"] else " [OFF]"
            print(f"{name[:34]+status:<35} {s['entries_new']:>5} "
                  f"{s['entries_total']:>6} {s['errors']:>4} {s['last_success']:>8}")
        
        print(f"\nDedup cache: {self.get_seen_count()} entries")
        print(f"{'='*65}\n")
