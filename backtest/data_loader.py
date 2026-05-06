"""
Candle data loader with local CSV cache.

Fetches 1-minute candles from Tinkoff API and caches them as CSV
files in data/backtest_cache/ so repeated backtests don't re-fetch.
"""

import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


# ── Candle dict keys: time, open, high, low, close, volume ────


def calculate_atr_from_candles(
    candles: list[dict],
    period: int = 14,
) -> float:
    """Compute ATR from candle dicts.  Same True Range formula as RiskManager."""
    if len(candles) < 2:
        if candles:
            return candles[-1]["high"] - candles[-1]["low"]
        return 0.0

    true_ranges: list[float] = []
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)

    if not true_ranges:
        return 0.0

    used = true_ranges[-period:] if len(true_ranges) >= period else true_ranges
    return sum(used) / len(used)


# ── CSV cache ─────────────────────────────────────────────────


_CSV_FIELDS = ("time", "open", "high", "low", "close", "volume")


def _save_csv(path: Path, candles: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS)
        writer.writeheader()
        for c in candles:
            writer.writerow({
                "time": c["time"].isoformat() if isinstance(c["time"], datetime) else c["time"],
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c.get("volume", 0),
            })


def _load_csv(path: Path) -> list[dict]:
    candles: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            candles.append({
                "time": datetime.fromisoformat(row["time"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row.get("volume", 0)),
            })
    return candles


# ── CandleCache ───────────────────────────────────────────────


class CandleCache:
    """Fetch and cache historical candles.

    Uses Tinkoff API on first request, then serves from CSV cache.
    """

    def __init__(
        self,
        client=None,
        cache_dir: Path = Path("data/backtest_cache"),
    ):
        self.client = client
        self.cache_dir = Path(cache_dir)

    def _cache_path(self, figi: str, day: date) -> Path:
        return self.cache_dir / f"{figi}_{day.isoformat()}.csv"

    async def get_candles(
        self,
        figi: str,
        day: date,
    ) -> list[dict]:
        """Return 1-min candles for *figi* on *day*.

        Loads from cache if available, otherwise fetches from API.
        """
        path = self._cache_path(figi, day)
        if path.exists():
            return _load_csv(path)

        if self.client is None:
            return []

        candles = await self._fetch_from_api(figi, day)
        if candles:
            _save_csv(path, candles)
        return candles

    async def _fetch_from_api(self, figi: str, day: date) -> list[dict]:
        from_dt = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        to_dt = from_dt + timedelta(days=1)

        try:
            from t_tech.invest import CandleInterval

            response = self.client.market_data.get_candles(
                figi=figi,
                from_=from_dt,
                to=to_dt,
                interval=CandleInterval.CANDLE_INTERVAL_1_MIN,
            )
        except Exception as e:
            print(f"Candle fetch error for {figi} on {day}: {e}")
            return []

        def q2f(q) -> float:
            return q.units + q.nano / 1e9

        result: list[dict] = []
        for c in response.candles:
            result.append({
                "time": c.time if hasattr(c, "time") else from_dt,
                "open": q2f(c.open),
                "high": q2f(c.high),
                "low": q2f(c.low),
                "close": q2f(c.close),
                "volume": getattr(c, "volume", 0),
            })
        return result

    def get_candles_range(
        self,
        figi: str,
        start: datetime,
        end: datetime,
    ) -> list[dict]:
        """Load cached candles for a datetime range (sync, from cache only)."""
        candles: list[dict] = []
        current = start.date()
        end_date = end.date()
        while current <= end_date:
            path = self._cache_path(figi, current)
            if path.exists():
                day_candles = _load_csv(path)
                candles.extend(
                    c for c in day_candles if start <= c["time"] <= end
                )
            current += timedelta(days=1)
        candles.sort(key=lambda c: c["time"])
        return candles
