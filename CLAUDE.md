# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Event-driven automated trading bot for the Russian stock market (MOEX) using the Tinkoff Investments API. Combines **news-driven NLP analysis** with **order flow validation** to detect and execute momentum trades in sandbox mode.

**Current stage:** Full pipeline — news parsing (RSS + hybrid NLP), order flow validation, execution with risk management, signal arbitration, multi-page Rich Live terminal dashboard, backtesting, and comprehensive test suite (409 tests).

## Development Setup

**Environment:**
```bash
source .venv/bin/activate
pip install -r requirements.txt
python main.py              # Launch dashboard (default)
python main.py --no-rss     # Launch without RSS feeds
python main.py --sandbox    # Run legacy sandbox debug script
python main.py --backtest signals.csv  # Run backtest on historical data
pytest tests/ -v            # Run test suite (409 tests)
pytest --cov=bot            # Run with coverage report
```

**Configuration:**
- Environment variables in `.env`
- `TOKEN_TINKOFF`: API token for Tinkoff sandbox access
- `GEMINI_API_KEY`: Google Gemini API key (optional, for LLM fallback in NLP)
- `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`: Telegram MTProto keys (optional)

**Safety:** Always use sandbox mode for development. Never commit production credentials.

## Architecture at a Glance

```
main.py → dashboard.py (Rich Live TUI, multi-page)
       └→ backtest/runner.py (--backtest mode)
                │
                ▼
          bot/core.py (TradingBot orchestrator)
           ┌────┼──────────────┐
           ▼    ▼              ▼
    listeners/ validators/ execution/
    ├─ news_parser.py   ├─ flow_analyzer.py   ├─ order_manager.py
    ├─ rss_listener.py  ├─ orderbook_stream.py├─ risk_manager.py
    └─ telegram_listener.py └─ trades_stream.py└─ position_tracker.py
           │
    bot/signal_arbiter.py (dedup + contradiction detection)
    bot/models.py (Position, NewsEvent, ExitSignal, etc.)
    bot/config.py (Config, TradingConfig, ValidationConfig — with __post_init__ validation)
    bot/utils/event_logger.py (structured logging with subscribers + log rotation)
    bot/utils/retry.py (async_retry, stream_with_reconnect, RateLimiter, AsyncRateLimiter)
    bot/utils/trade_db.py (SQLite persistence for closed positions)
```

## Key Modules

- **`dashboard.py`**: Multi-page Rich Live TUI. Page 1: signals/positions/log. Page 2: plotext charts. Page 3: trade history. Switch with `1`/`2`/`3` keys or `Tab`. Uses `tty.setcbreak()` for keystroke capture.
- **`bot/core.py`**: Central orchestrator. Wires signal arbiter, flow analyzer, order manager. Handles unified exit via `ExitSignal` callback and `_attempt_reversal()`.
- **`bot/signal_arbiter.py`**: Deduplication (time window) + contradiction detection against open positions.
- **`bot/listeners/news_parser.py`**: Hybrid NLP — keyword matching first, Gemini LLM fallback. Entity resolution via `data/entities.json` (ticker synonyms, regex patterns). Political/macro news handling with sector mapping.
- **`bot/listeners/rss_listener.py`**: Async RSS feed poller with dedup and freshness filtering.
- **`bot/validators/flow_analyzer.py`**: Validates signals via order book imbalance + trade volume spike + price movement. Has `validate_reversal()` for position flips.
- **`bot/execution/position_tracker.py`**: Monitors positions for SL/TP/time/trailing stop/momentum. Emits `ExitSignal` objects.
- **`bot/execution/risk_manager.py`**: ATR-based SL/TP, position sizing, trailing stop calculation, momentum candle check.
- **`bot/models.py`**: `Sentiment`, `Position` (with `PositionState`, `peak_price`), `NewsEvent`, `ExitSignal`, `ReversalContext`, `FlowAnalysis`.
- **`bot/utils/retry.py`**: `async_retry` decorator (exponential backoff for REST calls), `stream_with_reconnect` (auto-reconnect for gRPC streams), `RateLimiter` (sync, for Gemini), `AsyncRateLimiter` (async, for Tinkoff API).
- **`bot/utils/trade_db.py`**: `TradeDatabase` — SQLite persistence for closed positions. Loaded on startup, saved on every close. Survives restarts.
- **`backtest/`**: Backtesting module. `data_loader.py` (candle cache + ATR), `simulator.py` (walk-forward SL/TP/trailing/time simulation), `report.py` (stats + Rich display), `runner.py` (orchestrator + CSV signal loader).

## Data Files

- `data/whitelist.json` — ~40 liquid MOEX instruments (ticker → FIGI)
- `data/keywords.json` — Bullish/bearish keyword triggers (corporate + geopolitical)
- `data/entities.json` — Ticker synonyms, regex patterns, slang mappings
- `data/sectors.json` — Sector-to-ticker mapping for macro news
- `data/rss_feeds.json` — RSS feed URLs and polling intervals
- `data/trades.db` — SQLite database of closed positions (auto-created)
- `data/backtest_cache/` — Cached historical candles (CSV, one file per FIGI+date)

## Tinkoff API Patterns

- Use `with Client(token) as client:` context manager
- Access sandbox via `client.sandbox`, market data via `client.market_data`
- **FIGI** identifies instruments; **Lots** are the order unit
- **MoneyValue** has `units`, `nano`, `currency`; **Quotation** for prices
- Always use sandbox mode: `client.sandbox.post_sandbox_order()`

## Design Principles

- Async-first (`asyncio` everywhere)
- Event-driven pipeline: News → Parse → Validate (Order Flow) → Execute
- Unified exit path: all exit conditions emit `ExitSignal`, processed by single `_handle_exit` callback
- `PositionState` machine: `ACTIVE` → `REVERSING` → `CLOSED` (guards against race conditions)
- `RiskManager` is pure calculation; `PositionTracker` owns state mutation
- Dashboard is display-only; bot logic lives in `bot/`

## Resilience

- **Reconnection**: gRPC streams (`orderbook_stream`, `trades_stream`) auto-reconnect with exponential backoff via `stream_with_reconnect`. REST calls (`order_manager`, `poll()`) retry via `@async_retry` decorator.
- **Rate limiting**: Gemini API limited to 55 req/min via sync `RateLimiter`. Tinkoff API limited to 180 req/min via async `AsyncRateLimiter` shared across all modules.
- **Config validation**: `TradingConfig.__post_init__` and `ValidationConfig.__post_init__` enforce valid ranges. Dashboard `set` command validates and rolls back on error.
- **Graceful shutdown**: `SIGINT`/`SIGTERM` handled via `loop.add_signal_handler()`. `core.stop(close_positions=bool)` can optionally close all positions before exit.
- **Trade persistence**: Closed positions saved to SQLite (`data/trades.db`) via `TradeDatabase`. Loaded on startup so dashboard history and stats survive restarts.
- **Log rotation**: `EventLogger` deletes `.jsonl`/`.log` files older than 30 days on startup.
- **Concurrency**: `position_tracker._positions` protected by `asyncio.Lock`. `signal_arbiter` uses `threading.Lock`.

## Backtesting

```bash
python main.py --backtest signals.csv
```

**Signal CSV format:**
```csv
ticker,direction,timestamp
SBER,bullish,2026-03-15T10:30:00
GAZP,bearish,2026-03-15T11:00:00
```

Simulates position lifecycle on historical 1-min candles. Uses the same SL/TP/trailing stop/time limit logic as live trading. Candles fetched from Tinkoff API and cached in `data/backtest_cache/`. Report shows win rate, PnL, max drawdown, profit factor, per-trade log.

Order-flow validation is skipped in backtest mode — signals are assumed confirmed.

## Testing

409 tests across 12 test files. Run with `pytest tests/ -v`.

| Module | Coverage |
|--------|----------|
| `signal_arbiter.py` | 100% |
| `models.py` | 100% |
| `risk_manager.py` | 95% |
| `config.py` | 92% |
| `event_logger.py` | 83% |
| `news_parser.py` | 80% |
| `retry.py` | 82% |
| `trade_db.py` | 98% |
| `position_tracker.py` | 60% |
| `flow_analyzer.py` | 47% |
