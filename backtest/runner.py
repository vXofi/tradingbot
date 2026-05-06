"""
Backtest runner — orchestrates signal loading, candle fetching, simulation, and reporting.
"""

import asyncio
import csv
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from bot.config import Config
from bot.models import Sentiment

from .data_loader import CandleCache, calculate_atr_from_candles
from .report import BacktestReport
from .simulator import PositionSimulator, TradeResult


_SENTIMENT_MAP = {"bullish": Sentiment.BULLISH, "bearish": Sentiment.BEARISH}


@dataclass
class Signal:
    """A single trading signal to backtest."""

    ticker: str
    direction: Sentiment
    timestamp: datetime


class BacktestRunner:
    """Load signals, fetch candles, simulate, report."""

    def __init__(self, config: Config):
        self.config = config
        self.simulator = PositionSimulator(config.trading)
        self.cache: Optional[CandleCache] = None

    async def run(
        self,
        signals: list[Signal],
        client=None,
    ) -> list[TradeResult]:
        """Run backtest on a list of signals.

        If *client* is provided, missing candle data will be fetched from the
        Tinkoff API and cached.  Without a client, only cached data is used.
        """
        self.cache = CandleCache(client=client)
        results: list[TradeResult] = []

        for sig in signals:
            figi = self.config.get_figi(sig.ticker)
            if not figi:
                print(f"  skip {sig.ticker}: not in whitelist")
                continue

            # Fetch candles around the signal time
            buffer_min = self.config.trading.max_position_time_min + 30
            start = sig.timestamp - timedelta(hours=2)  # for ATR lookback
            end = sig.timestamp + timedelta(minutes=buffer_min)

            # Ensure cache has the needed days
            day = start.date()
            while day <= end.date():
                await self.cache.get_candles(figi, day)
                day += timedelta(days=1)

            # Load from cache
            candles = self.cache.get_candles_range(figi, start, end)
            if not candles:
                print(f"  skip {sig.ticker} @ {sig.timestamp}: no candle data")
                continue

            # ATR from candles before entry
            pre_entry = [c for c in candles if c["time"] < sig.timestamp]
            atr = calculate_atr_from_candles(pre_entry)
            if atr <= 0:
                atr = candles[0]["close"] * 0.01  # 1% fallback

            # Simulate
            result = self.simulator.simulate(
                candles=candles,
                direction=sig.direction,
                entry_time=sig.timestamp,
                atr=atr,
                ticker=sig.ticker,
            )
            if result:
                results.append(result)

        return results

    @staticmethod
    def load_signals_csv(path: Path) -> list[Signal]:
        """Load signals from CSV.

        Expected columns: ticker, direction, timestamp
        """
        signals: list[Signal] = []
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                direction = _SENTIMENT_MAP.get(row["direction"].strip().lower())
                if direction is None:
                    continue
                signals.append(Signal(
                    ticker=row["ticker"].strip().upper(),
                    direction=direction,
                    timestamp=datetime.fromisoformat(row["timestamp"].strip()),
                ))
        return signals


# ── CLI entry point ───────────────────────────────────────────


async def run_backtest_cli():
    """CLI handler for ``python main.py --backtest [signals.csv]``."""
    from rich.console import Console

    console = Console()

    # Find the CSV argument
    csv_path = None
    for arg in sys.argv:
        if arg.endswith(".csv"):
            csv_path = Path(arg)
            break

    if csv_path is None or not csv_path.exists():
        console.print("[red]Usage: python main.py --backtest signals.csv[/]")
        return

    console.print(f"[bold]Loading signals from {csv_path}...[/]")
    signals = BacktestRunner.load_signals_csv(csv_path)
    if not signals:
        console.print("[yellow]No valid signals found in CSV.[/]")
        return
    console.print(f"  {len(signals)} signal(s) loaded")

    config = Config.from_env()
    runner = BacktestRunner(config)

    # Connect to Tinkoff for candle fetching
    client = None
    try:
        from t_tech.invest import Client
        client_ctx = Client(config.tinkoff_token)
        client = client_ctx.__enter__()
        console.print("[bold]Fetching candle data...[/]")
    except Exception as e:
        console.print(f"[yellow]No API connection ({e}) — using cached data only[/]")

    try:
        results = await runner.run(signals, client=client)
    finally:
        if client is not None:
            try:
                client_ctx.__exit__(None, None, None)
            except Exception:
                pass

    if not results:
        console.print("[yellow]No trades simulated.[/]")
        return

    report = BacktestReport(results, config.trading)
    report.print_report(console)
