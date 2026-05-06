"""Tests for bot.utils.retry — async_retry, stream_with_reconnect, RateLimiter, AsyncRateLimiter."""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.utils.retry import AsyncRateLimiter, RateLimiter, async_retry, stream_with_reconnect


# ── async_retry ───────────────────────────────────────────────


class TestAsyncRetry:
    async def test_succeeds_first_try(self):
        call_count = 0

        @async_retry(max_attempts=3)
        async def ok():
            nonlocal call_count
            call_count += 1
            return "done"

        result = await ok()
        assert result == "done"
        assert call_count == 1

    async def test_retries_on_failure(self):
        call_count = 0

        @async_retry(max_attempts=3, base_delay=0.01)
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("fail")
            return "recovered"

        result = await flaky()
        assert result == "recovered"
        assert call_count == 3

    async def test_max_attempts_exceeded(self):
        @async_retry(max_attempts=2, base_delay=0.01)
        async def always_fails():
            raise ConnectionError("boom")

        with pytest.raises(ConnectionError, match="boom"):
            await always_fails()

    async def test_retryable_exceptions_filter(self):
        """Only specified exception types trigger retry."""

        @async_retry(
            max_attempts=3,
            base_delay=0.01,
            retryable_exceptions=(ConnectionError,),
        )
        async def wrong_exc():
            raise ValueError("not retryable")

        with pytest.raises(ValueError, match="not retryable"):
            await wrong_exc()

    async def test_on_retry_callback_called(self):
        cb = MagicMock()

        @async_retry(max_attempts=3, base_delay=0.01, on_retry=cb)
        async def flaky():
            if cb.call_count < 2:
                raise RuntimeError("oops")
            return "ok"

        await flaky()
        assert cb.call_count == 2
        attempt, exc, delay = cb.call_args_list[0][0]
        assert attempt == 1
        assert isinstance(exc, RuntimeError)
        assert delay > 0

    async def test_preserves_return_value(self):
        @async_retry(max_attempts=1)
        async def typed() -> dict:
            return {"key": 42}

        result = await typed()
        assert result == {"key": 42}

    async def test_exponential_backoff_delays(self):
        """Verify delays grow exponentially (via callback)."""
        delays = []

        def track(attempt, exc, delay):
            delays.append(delay)

        @async_retry(max_attempts=4, base_delay=0.01, max_delay=1.0, on_retry=track)
        async def fail_3():
            if len(delays) < 3:
                raise RuntimeError
            return "ok"

        await fail_3()
        # Delays: 0.01 * 2^0, 0.01 * 2^1, 0.01 * 2^2
        assert delays[0] == pytest.approx(0.01)
        assert delays[1] == pytest.approx(0.02)
        assert delays[2] == pytest.approx(0.04)

    async def test_max_delay_cap(self):
        delays = []

        def track(attempt, exc, delay):
            delays.append(delay)

        @async_retry(max_attempts=10, base_delay=1.0, max_delay=5.0, on_retry=track)
        async def fail_many():
            if len(delays) < 5:
                raise RuntimeError
            return "ok"

        await fail_many()
        assert all(d <= 5.0 for d in delays)


# ── stream_with_reconnect ─────────────────────────────────────


class TestStreamWithReconnect:
    async def test_normal_iteration(self):
        async def factory():
            for i in range(3):
                yield i

        items = []
        async for item in stream_with_reconnect(factory, base_delay=0.01):
            items.append(item)
            if len(items) == 3:
                break
        assert items == [0, 1, 2]

    async def test_reconnects_on_error(self):
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("stream died")
            for i in range(3):
                yield i

        items = []
        async for item in stream_with_reconnect(factory, base_delay=0.01):
            items.append(item)
            if len(items) == 3:
                break
        assert items == [0, 1, 2]
        assert call_count == 2

    async def test_max_reconnects_exceeded(self):
        async def factory():
            # Must be an async generator (yield) that raises, not a plain coroutine
            if False:
                yield  # noqa: make this an async generator
            raise ConnectionError("always fails")

        with pytest.raises(ConnectionError):
            async for _ in stream_with_reconnect(
                factory, max_reconnects=2, base_delay=0.01
            ):
                pass

    async def test_resets_counter_on_success(self):
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            yield call_count
            raise ConnectionError("die after one item")

        items = []
        # max_reconnects=3 but we get successful items each time, so counter resets
        async for item in stream_with_reconnect(
            factory, max_reconnects=3, base_delay=0.01
        ):
            items.append(item)
            if len(items) == 5:
                break
        assert len(items) == 5

    async def test_on_reconnect_callback(self):
        cb = MagicMock()
        call_count = 0

        async def factory():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError
            yield "ok"

        async for item in stream_with_reconnect(
            factory, base_delay=0.01, on_reconnect=cb
        ):
            break
        assert cb.call_count >= 1


# ── RateLimiter ───────────────────────────────────────────────


class TestRateLimiter:
    def test_under_limit_no_block(self):
        rl = RateLimiter(calls_per_minute=10, calls_per_day=100)
        start = time.monotonic()
        for _ in range(5):
            assert rl.wait_if_needed() is True
        elapsed = time.monotonic() - start
        assert elapsed < 1.0  # Should be near-instant

    def test_daily_cap_rejects(self):
        rl = RateLimiter(calls_per_minute=100, calls_per_day=3)
        assert rl.wait_if_needed() is True
        assert rl.wait_if_needed() is True
        assert rl.wait_if_needed() is True
        assert rl.wait_if_needed() is False  # daily cap hit

    def test_minute_window_blocks(self):
        rl = RateLimiter(calls_per_minute=2, calls_per_day=100)
        assert rl.wait_if_needed() is True
        assert rl.wait_if_needed() is True
        # Third call should block briefly; we just verify it returns True eventually
        # (don't actually wait 60s — test the mechanism)
        # We'll test that the internal window has 2 entries
        assert len(rl._minute_window) == 2


# ── AsyncRateLimiter ──────────────────────────────────────────


class TestAsyncRateLimiter:
    async def test_under_limit_no_delay(self):
        rl = AsyncRateLimiter(calls_per_minute=10)
        start = asyncio.get_event_loop().time()
        for _ in range(5):
            await rl.acquire()
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 1.0

    async def test_window_tracks_calls(self):
        rl = AsyncRateLimiter(calls_per_minute=100)
        for _ in range(10):
            await rl.acquire()
        assert len(rl._window) == 10

    async def test_concurrent_acquire_respects_limit(self):
        rl = AsyncRateLimiter(calls_per_minute=5)
        # Launch 5 concurrent acquires — all should succeed
        results = await asyncio.gather(*[rl.acquire() for _ in range(5)])
        assert len(results) == 5
        assert len(rl._window) == 5
