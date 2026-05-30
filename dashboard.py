#!/usr/bin/env python3
"""
Rich Live Dashboard — primary bot interface.

Usage:
    python dashboard.py              # Start bot + RSS + live dashboard
    python dashboard.py --no-rss     # Start without RSS listener
"""

import asyncio
import os
import sys
import termios
import threading
import tty
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from bot.config import Config
from bot.core import TradingBot
from bot.listeners.news_parser import NewsParser, ParseResult
from bot.listeners.rss_listener import RSSListener, FeedEntry
from bot.models import Sentiment
from bot.utils import EventLogger


# ── state ────────────────────────────────────────────────


@dataclass
class SignalRecord:
    time: datetime
    tickers: list[str]
    sentiment: Sentiment
    confidence: float
    method: str
    signal_type: str
    keywords: list[str]
    source: str
    matched: bool = True
    title: str = ""


PAGE_NAMES = ["DASHBOARD", "CHARTS", "HISTORY"]


@dataclass
class PnlSnapshot:
    time: datetime
    pnl: float


@dataclass
class DashboardState:
    bot: Optional[TradingBot] = None
    parser: Optional[NewsParser] = None
    rss: Optional[RSSListener] = None

    signals: deque = field(default_factory=lambda: deque(maxlen=100))
    log_events: deque = field(default_factory=lambda: deque(maxlen=50))

    # time-series for charts
    pnl_history: list = field(default_factory=list)
    signal_counts: dict = field(default_factory=lambda: {"bullish": 0, "bearish": 0, "neutral": 0})
    signal_timeline: deque = field(default_factory=lambda: deque(maxlen=60))

    start_time: datetime = field(default_factory=datetime.now)
    input_buffer: str = ""
    last_cmd: str = ""
    last_cmd_result: str = ""
    running: bool = True
    rss_enabled: bool = True
    current_page: int = 0


# ── layout builder ───────────────────────────────────────


def _uptime(state: DashboardState) -> str:
    delta = datetime.now() - state.start_time
    total = int(delta.total_seconds())
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def _build_header(state: DashboardState) -> Panel:
    bot = state.bot
    mode = "SB" if (bot and bot.config.use_sandbox) else "PROD"
    ev = bot._events_received if bot else 0
    val = bot._events_validated if bot else 0
    trades = bot._trades_executed if bot else 0

    pnl = 0.0
    if bot and bot.position_tracker:
        stats = bot.position_tracker.get_stats()
        pnl = stats.get("total_pnl", 0.0)

    rss_tag = "RSS" if state.rss_enabled else "noRSS"

    line = Text()
    # page tabs
    for i, name in enumerate(PAGE_NAMES):
        if i == state.current_page:
            line.append(f" {i+1}:{name} ", style="bold white on blue")
        else:
            line.append(f" {i+1}:{name} ", style="dim")
    line.append("  ", style="")
    line.append(f"{mode}", style="yellow")
    line.append(f" {rss_tag}", style="dim")
    line.append(f" {_uptime(state)}", style="cyan")
    matched = sum(1 for s in state.signals if s.matched)
    line.append(f"  S:{matched}/{len(state.signals)}", style="yellow")
    line.append(f" V:{val}", style="green")
    line.append(f" T:{trades}", style="blue")
    line.append(f" PnL:{pnl:+.2f}", style="bold green" if pnl >= 0 else "bold red")

    return Panel(line, style="bold", height=3)


def _build_signals(state: DashboardState) -> Panel:
    table = Table(box=None, expand=True, pad_edge=False, show_header=True, header_style="bold dim")
    table.add_column("Time", width=8, no_wrap=True)
    table.add_column("", width=3, no_wrap=True)
    table.add_column("Ticker/Headline", ratio=1, no_wrap=True, overflow="ellipsis")
    table.add_column("Info", width=20, no_wrap=True, overflow="ellipsis")

    matched_count = sum(1 for s in state.signals if s.matched)

    for sig in reversed(state.signals):
        t = sig.time.strftime("%H:%M:%S")

        if not sig.matched:
            table.add_row(
                Text(t, style="dim"),
                Text("·", style="dim"),
                Text(sig.title[:60] if sig.title else "—", style="dim"),
                Text(sig.source.split(":")[-1][:18], style="dim"),
            )
        else:
            style = {"bullish": "green", "bearish": "red"}.get(sig.sentiment.value, "dim")
            arrow = {"bullish": "▲", "bearish": "▼", "neutral": "·"}.get(sig.sentiment.value, "·")
            tickers_str = ", ".join(sig.tickers[:3])
            if len(sig.tickers) > 3:
                tickers_str += f"+{len(sig.tickers) - 3}"
            kw = ", ".join(sig.keywords[:2]) if sig.keywords else ""
            info = f"{sig.confidence:.0%} {sig.method}"
            if kw:
                info += f" {kw}"
            table.add_row(
                Text(t, style=style),
                Text(arrow, style=f"bold {style}"),
                Text(tickers_str, style=f"bold {style}"),
                Text(info, style=style),
            )

    total = len(state.signals)
    return Panel(table, title=f"NEWS FEED ({matched_count} signals / {total} items)", border_style="yellow", height=8)


def _build_positions(state: DashboardState) -> Panel:
    positions = []
    if state.bot and state.bot.position_tracker:
        positions = state.bot.position_tracker.get_all_positions()

    if not positions:
        content = Text("no open positions", style="dim italic", justify="center")
        return Panel(content, title="OPEN POSITIONS (0)", border_style="blue", height=6)

    table = Table(box=None, expand=True, pad_edge=False, show_header=True, header_style="bold dim")
    table.add_column("Ticker", width=6)
    table.add_column("Side", width=5)
    table.add_column("Entry", width=9, justify="right")
    table.add_column("Peak", width=9, justify="right")
    table.add_column("SL", width=9, justify="right")
    table.add_column("TP", width=9, justify="right")
    table.add_column("Qty", width=5, justify="right")
    table.add_column("Time", width=5, justify="right")
    table.add_column("St", width=3)

    state_icons = {"active": "●", "reversing": "↻", "closed": "○"}

    for pos in positions:
        elapsed = (datetime.now() - pos.entry_time).total_seconds() / 60
        side_style = "green" if pos.direction == Sentiment.BULLISH else "red"
        side = "LONG" if pos.direction == Sentiment.BULLISH else "SHORT"
        tp_str = f"{pos.take_profit:.2f}" if pos.take_profit else "---"
        st_icon = state_icons.get(pos.state.value, "?")
        st_style = "green" if pos.state.value == "active" else "yellow"
        table.add_row(
            pos.ticker,
            Text(side, style=f"bold {side_style}"),
            f"{pos.entry_price:.2f}",
            f"{pos.peak_price:.2f}",
            f"{pos.stop_loss:.2f}",
            tp_str,
            str(pos.quantity),
            f"{elapsed:.0f}m",
            Text(st_icon, style=st_style),
        )

    return Panel(table, title=f"OPEN POSITIONS ({len(positions)})", border_style="blue", height=6)


EVENT_STYLES = {
    "news_parsed": ("bold yellow", "NEWS"),
    "news_ignored": ("dim", "SKIP"),
    "signal_validated": ("bold green", "OK"),
    "signal_rejected": ("bold red", "REJ"),
    "signal_inconclusive": ("dim", "???"),
    "signal_duplicate": ("dim yellow", "DUP"),
    "signal_contradiction": ("bold magenta", "FLIP"),
    "order_placed": ("cyan", "ORD"),
    "order_failed": ("bold red", "FAIL"),
    "position_opened": ("bold green", "OPEN"),
    "position_closed": ("bold blue", "CLOSE"),
    "stop_loss_hit": ("bold red", "SL"),
    "take_profit_hit": ("bold green", "TP"),
    "time_limit_hit": ("yellow", "TIME"),
    "trailing_stop_hit": ("bold red", "TRAIL"),
    "momentum_exit": ("bold red", "MOM"),
    "position_reversed": ("bold magenta", "REV"),
    "error": ("bold red", "ERR"),
}


def _build_log(state: DashboardState) -> Panel:
    table = Table(box=None, expand=True, pad_edge=False, show_header=True, header_style="bold dim")
    table.add_column("Time", width=8, no_wrap=True)
    table.add_column("Type", width=5, no_wrap=True)
    table.add_column("Ticker", width=14, no_wrap=True, overflow="ellipsis")
    table.add_column("Details", ratio=1, no_wrap=True, overflow="ellipsis")

    for ev in reversed(state.log_events):
        ts = ev.get("timestamp", "")
        try:
            time_str = ts.split("T")[1].split(".")[0]
        except (IndexError, AttributeError):
            time_str = str(ts)[:8]

        etype = ev.get("event_type", "")
        style, label = EVENT_STYLES.get(etype, ("dim", etype[:5]))
        ticker = ev.get("ticker", "")
        if len(ticker) > 14:
            ticker = ticker[:12] + ".."

        parts = []
        sent = ev.get("sentiment")
        if sent:
            parts.append(sent)
        conf = ev.get("confidence")
        if conf is not None:
            parts.append(f"{conf:.0%}")
        pnl = ev.get("pnl")
        if pnl is not None:
            parts.append(f"PnL:{pnl:+.2f}")
        reason = ev.get("reason")
        if reason:
            parts.append(reason[:30])

        table.add_row(
            time_str,
            Text(label, style=style),
            ticker,
            Text(" ".join(parts), style="dim"),
        )

    count = len(state.log_events)
    return Panel(table, title=f"EVENT LOG ({count})", border_style="cyan")


def _build_cmdbar(state: DashboardState) -> Panel:
    grid = Table.grid(expand=True)
    grid.add_column(ratio=1)

    prompt = Text()
    prompt.append("> ", style="bold green")
    prompt.append(state.input_buffer, style="bold")
    prompt.append("_", style="blink bold green")

    result = Text(overflow="ellipsis", no_wrap=True)
    if state.last_cmd:
        result.append(state.last_cmd, style="dim")
        if state.last_cmd_result:
            result.append(f"  {state.last_cmd_result}", style="italic dim")
    else:
        result.append("type 'help' for commands", style="dim")

    grid.add_row(prompt)
    grid.add_row(result)
    return Panel(grid, title="INPUT", border_style="green", height=4)


def _build_charts_page(state: DashboardState) -> Panel:
    """Page 2: plotext charts for signal activity and PnL."""
    import plotext as plt
    import shutil

    # Fit charts to terminal width minus borders/padding
    term_width = shutil.get_terminal_size((100, 30)).columns
    chart_width = max(60, term_width - 4)

    content_parts = []

    try:
        # -- signal timeline bar chart --
        plt.clf()
        plt.theme("dark")
        plt.plot_size(chart_width, 12)
        plt.title("Signals (last 30)")

        if state.signal_timeline:
            recent = list(state.signal_timeline)[-30:]
            labels = [t.strftime("%H:%M") for t, _ in recent]
            colors_map = {"bullish": "green", "bearish": "red", "neutral": "gray"}
            values = [1] * len(recent)
            colors = [colors_map.get(s, "gray") for _, s in recent]
            plt.simple_bar(labels, values, color=colors)
        else:
            plt.simple_bar(["no signals yet"], [0])

        content_parts.append(plt.build())

        # -- PnL line chart --
        plt.clf()
        plt.theme("dark")
        plt.plot_size(chart_width, 12)
        plt.title("Cumulative PnL")

        if state.pnl_history and len(state.pnl_history) > 0:
            vals = [s.pnl for s in state.pnl_history]
            xs = list(range(len(vals)))
            plt.plot(xs, vals, color="green" if vals[-1] >= 0 else "red")
        else:
            plt.plot([0, 1], [0, 0])
            plt.title("PnL — no trades yet")

        content_parts.append(plt.build())

        # -- signal breakdown --
        plt.clf()
        plt.theme("dark")
        plt.plot_size(chart_width, 8)
        plt.title("Signal Breakdown")
        labels = ["Bullish", "Bearish", "Neutral"]
        vals = [
            state.signal_counts.get("bullish", 0),
            state.signal_counts.get("bearish", 0),
            state.signal_counts.get("neutral", 0),
        ]
        if any(v > 0 for v in vals):
            plt.simple_bar(labels, vals, color=["green", "red", "gray"])
        else:
            plt.simple_bar(["no data"], [1])

        content_parts.append(plt.build())

        combined = Text.from_ansi("\n\n".join(content_parts))
        return Panel(combined, title="CHARTS", border_style="magenta")

    except Exception as e:
        return Panel(
            Text(f"Chart render error: {type(e).__name__}: {e}", style="red"),
            title="CHARTS",
            border_style="red",
        )


def _build_history_page(state: DashboardState) -> Panel:
    """Page 3: detailed trade history table."""
    table = Table(box=None, expand=True, pad_edge=False, show_header=True, header_style="bold dim")
    table.add_column("#", width=4, justify="right")
    table.add_column("Time", width=18, no_wrap=True)
    table.add_column("Ticker", width=8, no_wrap=True)
    table.add_column("Side", width=5, no_wrap=True)
    table.add_column("Entry", width=10, justify="right", no_wrap=True)
    table.add_column("Exit", width=10, justify="right", no_wrap=True)
    table.add_column("PnL", width=10, justify="right", no_wrap=True)
    table.add_column("Reason", width=12, no_wrap=True, overflow="ellipsis")
    table.add_column("Duration", width=8, justify="right", no_wrap=True)

    if state.bot and state.bot.position_tracker:
        trades = state.bot.position_tracker.get_history(50)
        for i, t in enumerate(reversed(trades), 1):
            pnl_str = f"{t.pnl:+.2f}" if t.pnl is not None else "—"
            pnl_style = "green" if (t.pnl or 0) >= 0 else "red"
            side = "LONG" if t.direction == Sentiment.BULLISH else "SHORT"
            side_style = "green" if t.direction == Sentiment.BULLISH else "red"
            entry_t = t.entry_time.strftime("%m-%d %H:%M:%S") if hasattr(t, "entry_time") and t.entry_time else "—"
            exit_p = f"{t.exit_price:.2f}" if hasattr(t, "exit_price") and t.exit_price else "—"
            dur = ""
            if hasattr(t, "entry_time") and hasattr(t, "exit_time") and t.entry_time and t.exit_time:
                d = (t.exit_time - t.entry_time).total_seconds()
                dur = f"{d/60:.1f}m" if d < 3600 else f"{d/3600:.1f}h"
            reason = t.exit_reason if hasattr(t, "exit_reason") and t.exit_reason else "—"
            table.add_row(
                str(i),
                entry_t,
                t.ticker,
                Text(side, style=f"bold {side_style}"),
                f"{t.entry_price:.2f}",
                exit_p,
                Text(pnl_str, style=f"bold {pnl_style}"),
                reason,
                dur,
            )

    if not table.row_count:
        return Panel(
            Text("no trade history yet", style="dim italic", justify="center"),
            title="TRADE HISTORY (0)",
            border_style="blue",
        )

    return Panel(table, title=f"TRADE HISTORY ({table.row_count})", border_style="blue")


def build_layout(state: DashboardState) -> Layout:
    layout = Layout()

    if state.current_page == 0:
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="signals", size=8),
            Layout(name="positions", size=6),
            Layout(name="log", ratio=1),
            Layout(name="cmdbar", size=4),
        )
        layout["signals"].update(_build_signals(state))
        layout["positions"].update(_build_positions(state))
        layout["log"].update(_build_log(state))
    elif state.current_page == 1:
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="charts", ratio=1),
            Layout(name="cmdbar", size=4),
        )
        layout["charts"].update(_build_charts_page(state))
    else:
        layout.split_column(
            Layout(name="header", size=3),
            Layout(name="history", ratio=1),
            Layout(name="cmdbar", size=4),
        )
        layout["history"].update(_build_history_page(state))

    layout["header"].update(_build_header(state))
    layout["cmdbar"].update(_build_cmdbar(state))
    return layout


# ── command processing ───────────────────────────────────


def command_reader(
    state: DashboardState,
    queue: asyncio.Queue,
    loop: asyncio.AbstractEventLoop,
):
    """Background thread: reads keystrokes via cbreak mode, builds input buffer."""
    fd = sys.stdin.fileno()
    while state.running:
        try:
            ch = os.read(fd, 1)
            if not ch:
                break

            if ch in (b"\r", b"\n"):
                line = state.input_buffer.strip()
                state.input_buffer = ""
                if line:
                    loop.call_soon_threadsafe(queue.put_nowait, line)
                continue

            if ch in (b"\x03", b"\x04"):
                state.running = False
                break

            if ch in (b"\x7f", b"\x08"):
                state.input_buffer = state.input_buffer[:-1]
                continue

            # Tab → next page
            if ch == b"\t":
                state.current_page = (state.current_page + 1) % len(PAGE_NAMES)
                continue

            if ch == b"\x1b":
                os.read(fd, 2)
                continue

            # 1/2/3 switch pages directly (only when input buffer is empty)
            if ch in (b"1", b"2", b"3") and not state.input_buffer:
                state.current_page = ch[0] - ord("1")
                continue

            if 32 <= ch[0] < 127:
                state.input_buffer += ch.decode("ascii")

        except (EOFError, OSError):
            break


_SETTABLE_PARAMS = {
    "trailing_stop_pct": float,
    "momentum_candles": int,
    "momentum_bearish_ratio": float,
    "momentum_check_interval_sec": float,
    "monitor_interval_sec": float,
    "dedup_window_sec": float,
    "max_position_rub": float,
    "stop_loss_atr_mult": float,
    "take_profit_atr_mult": float,
    "max_position_time_min": int,
}


async def process_command(state: DashboardState, line: str) -> str:
    """Process a CLI command. Returns a short result string for the status bar."""
    parts = line.strip().split()
    if not parts:
        return ""

    cmd = parts[0].lower()
    args = parts[1:]
    bot = state.bot

    # ── lifecycle ────────────────────────────────────────
    if cmd in ("quit", "exit", "q"):
        state.running = False
        return "shutting down..."

    if cmd == "help":
        return (
            "news/trade <txt>  signal <T> <dir>  analyze <T>  "
            "close/closeall  stats  history  config  set <p> <v>  rss  "
            "page 1/2/3 (or Tab)  quit"
        )

    # ── news / trading ──────────────────────────────────
    if cmd == "news" and args:
        text = " ".join(args)
        result = state.parser.parse(text, source="cli")
        if result.success and result.tickers:
            _record_signal(state, result, "cli")
            return f"{', '.join(result.tickers)} {result.sentiment.value} {result.confidence:.0%}"
        return "no signal detected"

    if cmd == "trade" and args:
        text = " ".join(args)
        result = state.parser.parse(text, source="cli")
        if not result.success or not result.tickers:
            return "no signal detected"
        if result.sentiment == Sentiment.NEUTRAL:
            return "neutral -- not trading"
        _record_signal(state, result, "cli")
        events = state.parser.to_news_events(result, source="cli")
        for event in events:
            await bot.process_event(event)
        return f"executed {len(events)} signal(s)"

    if cmd == "signal" and len(args) >= 2:
        ticker = args[0].upper()
        sentiment = args[1].lower()
        keywords = args[2:] if len(args) > 2 else []
        event = bot.create_manual_event(ticker, sentiment, keywords)
        if event:
            await bot.process_event(event)
            return f"{ticker} {sentiment} -> processed"
        return "unknown ticker or sentiment"

    if cmd == "analyze" and args:
        ticker = args[0].upper()
        figi = bot.config.get_figi(ticker)
        if not figi:
            return f"unknown ticker: {ticker}"
        analysis = await bot.flow_analyzer.analyze(figi)
        return (
            f"{ticker} imb:{analysis.imbalance:+.2f} "
            f"vol:{analysis.volume_ratio:.1f}x "
            f"{analysis.detected_sentiment.value}"
        )

    # ── position management ─────────────────────────────
    if cmd == "close" and args:
        ticker = args[0].upper()
        figi = bot.config.get_figi(ticker)
        if not figi:
            return f"unknown ticker: {ticker}"
        if not bot.position_tracker.has_position(figi):
            return f"no open position for {ticker}"
        ok = await bot.order_manager.close_position_market(figi, "MANUAL")
        return f"{ticker} closed" if ok else f"{ticker} close failed"

    if cmd == "closeall":
        if not bot.position_tracker:
            return "no tracker"
        positions = bot.position_tracker.get_all_positions()
        if not positions:
            return "no open positions"
        closed = 0
        for pos in list(positions):
            ok = await bot.order_manager.close_position_market(pos.figi, "MANUAL")
            if ok:
                closed += 1
        return f"closed {closed}/{len(positions)} positions"

    if cmd == "positions":
        if not bot.position_tracker:
            return "no tracker"
        n = bot.position_tracker.get_positions_count()
        if n == 0:
            return "no open positions"
        lines = []
        for pos in bot.position_tracker.get_all_positions():
            elapsed = (datetime.now() - pos.entry_time).total_seconds() / 60
            side = "L" if pos.direction == Sentiment.BULLISH else "S"
            lines.append(
                f"{pos.ticker}({side}) @{pos.entry_price:.2f} "
                f"pk:{pos.peak_price:.2f} {elapsed:.0f}m"
            )
        return " | ".join(lines)

    # ── statistics / history ────────────────────────────
    if cmd == "stats":
        if not bot.position_tracker:
            return "no tracker"
        s = bot.position_tracker.get_stats()
        d = bot.risk_manager.get_daily_stats() if bot.risk_manager else {}
        return (
            f"Trades:{s['total_trades']} "
            f"WR:{s['win_rate']:.0%} "
            f"PnL:{s['total_pnl']:+.2f} "
            f"Best:{s['best_trade']:+.2f} "
            f"Worst:{s['worst_trade']:+.2f} "
            f"DayPnL:{d.get('daily_pnl', 0):+.2f}"
        )

    if cmd == "history":
        if not bot.position_tracker:
            return "no tracker"
        limit = int(args[0]) if args and args[0].isdigit() else 5
        trades = bot.position_tracker.get_history(limit)
        if not trades:
            return "no trade history"
        lines = []
        for t in trades[-limit:]:
            pnl_str = f"{t.pnl:+.2f}" if t.pnl is not None else "?"
            side = "L" if t.direction == Sentiment.BULLISH else "S"
            lines.append(f"{t.ticker}({side}) {pnl_str} [{t.exit_reason}]")
        return " | ".join(lines)

    # ── configuration ───────────────────────────────────
    if cmd == "config":
        tc = bot.config.trading
        return (
            f"trail:{tc.trailing_stop_pct}% "
            f"mom:{tc.momentum_candles}c/{tc.momentum_bearish_ratio:.0%} "
            f"dedup:{tc.dedup_window_sec}s "
            f"SL:{tc.stop_loss_atr_mult}x "
            f"TP:{tc.take_profit_atr_mult}x "
            f"maxTime:{tc.max_position_time_min}m "
            f"maxPos:{tc.max_position_rub}R"
        )

    if cmd == "set" and len(args) >= 2:
        param = args[0]
        if param not in _SETTABLE_PARAMS:
            return f"unknown param: {param} (try: {', '.join(_SETTABLE_PARAMS)})"
        try:
            cast = _SETTABLE_PARAMS[param]
            value = cast(args[1])
        except ValueError:
            return f"invalid value: {args[1]} (expected {_SETTABLE_PARAMS[param].__name__})"
        old_value = getattr(bot.config.trading, param)
        setattr(bot.config.trading, param, value)
        try:
            bot.config.trading.__post_init__()
        except (ValueError, TypeError) as e:
            setattr(bot.config.trading, param, old_value)
            return f"invalid: {e}"
        if param == "dedup_window_sec" and bot.signal_arbiter:
            from datetime import timedelta
            bot.signal_arbiter._window = timedelta(seconds=value)
        return f"{param} = {value}"

    # ── RSS control ─────────────────────────────────────
    if cmd == "rss":
        if not args:
            return f"RSS {'ON' if state.rss_enabled else 'OFF'}"
        sub = args[0].lower()
        if sub == "off" and state.rss and state.rss_enabled:
            state.rss.stop()
            state.rss_enabled = False
            return "RSS stopped"
        if sub == "on" and state.rss and not state.rss_enabled:
            state.rss_enabled = True
            asyncio.create_task(state.rss.start())
            return "RSS started"
        if sub == "status" and state.rss:
            stats = state.rss.get_feed_stats()
            parts = []
            for name, s in stats.items():
                parts.append(f"{name[:20]}:{s['entries_new']}new/{s['entries_total']}tot/{s['errors']}err")
            return " | ".join(parts) if parts else "no feeds"
        return f"RSS {'ON' if state.rss_enabled else 'OFF'} (use: rss on/off/status)"

    # ── misc ────────────────────────────────────────────
    if cmd == "page" and args:
        try:
            p = int(args[0]) - 1
            if 0 <= p < len(PAGE_NAMES):
                state.current_page = p
                return f"page → {PAGE_NAMES[p]}"
        except ValueError:
            pass
        return f"page 1-{len(PAGE_NAMES)} (or press Tab / number key)"

    if cmd == "clear":
        state.signals.clear()
        state.log_events.clear()
        state.last_cmd = ""
        state.last_cmd_result = ""
        return "buffers cleared"

    if cmd == "status":
        st = bot.get_status()
        return (
            f"{'running' if st['running'] else 'stopped'} | "
            f"{st['events_received']} events | "
            f"{st['trades_executed']} trades"
        )

    if cmd == "whitelist":
        n = len(bot.config.whitelist)
        return f"{n} instruments in whitelist"

    return f"unknown command: {cmd} (type 'help')"


# ── RSS integration ──────────────────────────────────────


def _record_signal(state: DashboardState, result: ParseResult, source: str):
    state.signals.append(SignalRecord(
        time=datetime.now(),
        tickers=result.tickers,
        sentiment=result.sentiment,
        confidence=result.confidence,
        method=result.method.value,
        signal_type=result.signal_type,
        keywords=result.keywords_found,
        source=source,
    ))
    state.signal_counts[result.sentiment.value] = (
        state.signal_counts.get(result.sentiment.value, 0) + 1
    )
    state.signal_timeline.append((datetime.now(), result.sentiment.value))


async def on_rss_entry(state: DashboardState, entry: FeedEntry):
    """RSS callback: parse entry, record signal, and forward to bot for execution."""
    if not state.parser:
        return

    result = state.parser.parse(entry.full_text, source=f"rss:{entry.feed_name}")
    source_tag = f"rss:{entry.feed_name}"

    if not result.success or not result.tickers:
        state.signals.append(SignalRecord(
            time=datetime.now(),
            tickers=[],
            sentiment=Sentiment.NEUTRAL,
            confidence=0.0,
            method="",
            signal_type="",
            keywords=[],
            source=source_tag,
            matched=False,
            title=entry.title[:80],
        ))
        return

    _record_signal(state, result, source_tag)

    if not state.bot:
        return

    state.bot.logger.news_parsed(
        ticker=", ".join(result.tickers),
        sentiment=result.sentiment.value,
        confidence=result.confidence,
        keywords=result.keywords_found,
        method=result.method.value,
    )

    if result.sentiment == Sentiment.NEUTRAL:
        return

    MAX_TICKERS_PER_EVENT = 5
    events = state.parser.to_news_events(result, source=source_tag)
    events = events[:MAX_TICKERS_PER_EVENT]

    tasks = [state.bot.process_event(event) for event in events]
    await asyncio.gather(*tasks, return_exceptions=True)


# ── main ─────────────────────────────────────────────────


async def main():
    use_rss = "--no-rss" not in sys.argv
    console = Console()

    console.print("[bold]Starting dashboard...[/]")

    state = DashboardState(rss_enabled=use_rss)

    def on_log_event(event_dict: dict):
        state.log_events.append(event_dict)

    config = Config.from_env()
    state.bot = TradingBot(config)
    state.bot.logger.console_output = False
    state.bot.logger.subscribe(on_log_event)
    await state.bot.start()

    state.parser = NewsParser(use_llm_fallback=True)

    rss_task = None
    if use_rss:
        state.rss = RSSListener(
            feeds_config_path=Path("data/rss_feeds.json"),
            logger=state.bot.logger,
        )
        state.rss.on_entry(lambda entry: on_rss_entry(state, entry))
        rss_task = asyncio.create_task(state.rss.start())

    is_tty = sys.stdin.isatty()
    old_term = None

    if is_tty:
        fd = sys.stdin.fileno()
        old_term = termios.tcgetattr(fd)
        tty.setcbreak(fd)
    else:
        console.print("[yellow]Not a TTY — interactive input disabled[/]")

    cmd_queue: asyncio.Queue[str] = asyncio.Queue()
    loop = asyncio.get_event_loop()

    # Graceful shutdown on SIGINT / SIGTERM
    import signal as _signal
    for _sig in (_signal.SIGINT, _signal.SIGTERM):
        loop.add_signal_handler(_sig, lambda: setattr(state, 'running', False))

    if is_tty:
        reader_thread = threading.Thread(
            target=command_reader, args=(state, cmd_queue, loop), daemon=True,
        )
        reader_thread.start()

    last_pnl_snapshot = 0.0
    _last_layout_error = 0.0

    try:
        with Live(
            build_layout(state),
            console=console,
            refresh_per_second=10,
        ) as live:
            while state.running:
                while not cmd_queue.empty():
                    try:
                        line = cmd_queue.get_nowait()
                        state.last_cmd = line
                        state.last_cmd_result = await process_command(state, line)
                    except asyncio.QueueEmpty:
                        break

                now = datetime.now().timestamp()
                if now - last_pnl_snapshot >= 30:
                    last_pnl_snapshot = now
                    pnl = 0.0
                    if state.bot and state.bot.position_tracker:
                        stats = state.bot.position_tracker.get_stats()
                        pnl = stats.get("total_pnl", 0.0)
                    state.pnl_history.append(PnlSnapshot(time=datetime.now(), pnl=pnl))
                    if len(state.pnl_history) > 200:
                        state.pnl_history = state.pnl_history[-200:]

                try:
                    live.update(build_layout(state))
                except Exception:
                    if now - _last_layout_error > 5:
                        _last_layout_error = now
                        live.update(Panel("[red]render error — retrying[/]"))

                await asyncio.sleep(0.05)

    finally:
        if old_term is not None:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_term)

        state.running = False
        if state.rss:
            state.rss.stop()
        if rss_task and not rss_task.done():
            rss_task.cancel()
            try:
                await rss_task
            except asyncio.CancelledError:
                pass
        await state.bot.stop()

    console.print("[bold]Dashboard stopped.[/]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
