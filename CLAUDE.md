# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Event-driven automated trading bot for the Russian stock market (MOEX) using the Tinkoff Investments API. Combines **news-driven NLP analysis** with **order flow validation** to detect and execute momentum trades in sandbox mode.

**Current stage:** Full pipeline — news parsing (RSS + hybrid NLP), order flow validation, execution with risk management, signal arbitration, multi-page Rich Live terminal dashboard, backtesting, offline NLP eval (rules + Gemini compare), portfolio docs, and test suite (~439 tests, ~61% coverage on `bot/`).

## Development Setup

**Environment:**
```bash
source .venv/bin/activate
pip install -r requirements.txt
python main.py              # Launch dashboard (default)
python main.py --no-rss     # Launch without RSS feeds
python main.py --sandbox    # Run legacy sandbox debug script
python main.py --backtest signals.csv  # Run backtest on historical data
pytest tests/ -v            # Run test suite (~439 tests; 2 skipped without Gemini)
pytest --cov=bot tests/     # Coverage report (~61% on bot/)
python -m bot.eval.nlp_metrics --compare  # Rules vs LLM on hard corpus
```

**Configuration:**
- Environment variables in `.env`
- `TOKEN_TINKOFF`: API token for Tinkoff sandbox access
- `GEMINI_API_KEY`: Google Gemini API key (optional, LLM fallback; free-tier Flash models only)
- `GEMINI_MODEL`: Optional override (default candidates in `bot/gemini_config.py`)
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
- **`bot/gemini_config.py`**: Free-tier model list (`gemini-2.5-flash-lite`, `3.x` previews), `probe_gemini()`, region/quota-aware errors.
- **`bot/eval/nlp_metrics.py`**: Offline NLP benchmark — `data/nlp_eval.json` (easy), `data/nlp_eval_llm.json` (hard), `--compare` for rules vs hybrid.
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
- `data/backtest_signals_sample.csv` — Sample signals for backtest demo
- `data/nlp_eval.json` — 38 hand-labeled headlines (rules regression)
- `data/nlp_eval_llm.json` — 12 hard headlines (rules vs Gemini compare)

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

~439 tests across 16 test files (`pytest tests/ -v`). Overall `bot/` coverage ~61% (`pytest --cov=bot tests/`).

| Module / area | Notes |
|---------------|--------|
| `signal_arbiter.py`, `models.py` | 100% |
| `trade_db.py`, `risk_manager.py`, `config.py` | 92–98% |
| `news_parser.py`, `retry.py`, `event_logger.py` | 80–83% |
| `test_core.py` | TradingBot orchestration (mocked) |
| `test_nlp_eval.py` | Rules corpus regression (38 samples) |
| `test_nlp_eval_llm.py` | Hard corpus + Gemini (`@pytest.mark.llm`, skipped without API) |
| `test_e2e_pipeline.py` | Parse → arbiter → mock flow → order |
| `test_backtest.py` | Simulator, candle cache, timezone normalization |
| `core.py`, gRPC streams | Low coverage (needs live/mock Tinkoff) |

**NLP eval (documented):** easy corpus rules 100% accuracy; hard corpus rules 20% → hybrid 60% (5 LLM calls). See `docs/nlp_eval_report.md`.

**CI:** GitHub Actions may fail outside Russia — `t-tech-investments` geo-restricted. Validate locally with `pytest`.

**Backtest sample:** `python main.py --backtest data/backtest_signals_sample.csv` — see `docs/backtest_report.md` (not real PnL).
