"""
Retry, reconnection, and rate-limiting utilities.

Reuses the exponential-backoff pattern from rss_listener.py:
    delay = min(base_delay * 2 ** consecutive_errors, max_delay)

Three primitives:
    - async_retry       — decorator for async REST calls
    - stream_with_reconnect — async-generator wrapper for gRPC streams
    - RateLimiter       — sliding-window rate limiter (sync)
"""

import asyncio
import functools
import time


def _is_not_found_error(exc: Exception) -> bool:
    """Return True for gRPC NOT_FOUND (status 5) — permanent, never retry."""
    msg = str(exc).lower()
    return "not_found" in msg or "50002" in msg or "(5," in str(exc)
from collections import deque
from typing import (
    AsyncIterator,
    Callable,
    Optional,
    TypeVar,
)

T = TypeVar("T")


# ── async_retry decorator ────────────────────────────────────


def async_retry(
    max_attempts: int = 3,
    base_delay: float = 2.0,
    max_delay: float = 300.0,
    retryable_exceptions: tuple = (Exception,),
    on_retry: Optional[Callable] = None,
):
    """Decorator that retries an async function with exponential backoff.

    Args:
        max_attempts: Total call attempts (1 = no retry).
        base_delay: Initial delay in seconds before first retry.
        max_delay: Cap on the delay.
        retryable_exceptions: Only these exception types trigger a retry.
        on_retry: Optional ``callback(attempt, exception, delay)`` called
            before each sleep so callers can log via EventLogger.
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except retryable_exceptions as exc:
                    last_exc = exc
                    if attempt + 1 >= max_attempts or _is_not_found_error(exc):
                        raise
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    if on_retry is not None:
                        try:
                            on_retry(attempt + 1, exc, delay)
                        except Exception:
                            pass
                    await asyncio.sleep(delay)
            raise last_exc  # unreachable, but keeps type-checkers happy

        return wrapper

    return decorator


# ── stream_with_reconnect ─────────────────────────────────────


async def stream_with_reconnect(
    stream_factory: Callable[[], AsyncIterator[T]],
    *,
    max_reconnects: int = -1,
    base_delay: float = 2.0,
    max_delay: float = 300.0,
    on_reconnect: Optional[Callable] = None,
    on_error: Optional[Callable] = None,
) -> AsyncIterator[T]:
    """Wrap an async-iterator factory with automatic reconnection.

    On any exception the wrapper sleeps with exponential backoff and then
    calls *stream_factory* again to obtain a fresh iterator.

    Args:
        stream_factory: Zero-arg callable returning an ``AsyncIterator[T]``.
        max_reconnects: -1 for unlimited; otherwise stop after this many
            consecutive reconnection failures and re-raise.
        base_delay / max_delay: Backoff parameters (same formula as RSS).
        on_reconnect: Optional ``callback(attempt, delay)`` for logging.
        on_error: Optional ``callback(attempt, exception)`` for logging.
    """

    consecutive_errors = 0

    while True:
        try:
            async for item in stream_factory():
                consecutive_errors = 0
                yield item
            # Stream ended cleanly (server closed) — reconnect.
        except Exception as exc:
            consecutive_errors += 1

            if 0 < max_reconnects < consecutive_errors:
                raise

            if on_error is not None:
                try:
                    on_error(consecutive_errors, exc)
                except Exception:
                    pass

        delay = min(base_delay * (2 ** consecutive_errors), max_delay)

        if on_reconnect is not None:
            try:
                on_reconnect(consecutive_errors, delay)
            except Exception:
                pass

        await asyncio.sleep(delay)


# ── RateLimiter ───────────────────────────────────────────────


class RateLimiter:
    """Synchronous sliding-window rate limiter.

    Designed for the Gemini free tier (60 req/min).  Uses ``time.sleep``
    which is fine because the LLM call itself takes 1-3 s.
    """

    def __init__(
        self,
        calls_per_minute: int = 55,
        calls_per_day: int = 1500,
    ):
        self._per_minute = calls_per_minute
        self._per_day = calls_per_day
        self._minute_window: deque[float] = deque()
        self._day_window: deque[float] = deque()

    def _prune(self, window: deque, horizon: float, now: float):
        while window and window[0] < now - horizon:
            window.popleft()

    def wait_if_needed(self) -> bool:
        """Block until a slot is available.  Returns False if daily cap hit."""
        now = time.monotonic()

        # Daily cap (hard reject — don't spin-wait for 24 h).
        self._prune(self._day_window, 86400.0, now)
        if len(self._day_window) >= self._per_day:
            return False

        # Per-minute window: spin until a slot opens.
        while True:
            now = time.monotonic()
            self._prune(self._minute_window, 60.0, now)
            if len(self._minute_window) < self._per_minute:
                break
            # Sleep until the oldest entry expires.
            sleep_for = self._minute_window[0] + 60.0 - now + 0.05
            if sleep_for > 0:
                time.sleep(sleep_for)

        now = time.monotonic()
        self._minute_window.append(now)
        self._day_window.append(now)
        return True


# ── AsyncRateLimiter ──────────────────────────────────────────


class AsyncRateLimiter:
    """Async sliding-window rate limiter for Tinkoff API calls.

    Uses ``asyncio.sleep`` so it never blocks the event loop.
    """

    def __init__(self, calls_per_minute: int = 180):
        self._per_minute = calls_per_minute
        self._window: deque[float] = deque()
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def acquire(self):
        """Wait until a rate-limit slot is available."""
        async with self._get_lock():
            loop = asyncio.get_event_loop()
            now = loop.time()

            while self._window and self._window[0] < now - 60.0:
                self._window.popleft()

            if len(self._window) >= self._per_minute:
                sleep_for = self._window[0] + 60.0 - now + 0.05
                if sleep_for > 0:
                    await asyncio.sleep(sleep_for)

            self._window.append(asyncio.get_event_loop().time())
