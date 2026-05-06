# Event-Driven Trading Bot (MOEX)

Automated trading bot for the Russian stock market (MOEX) using the [Tinkoff Invest API](https://www.tinkoff.ru/invest/). Combines **news-driven NLP analysis** with **order flow validation** to detect and execute momentum trades in sandbox mode.

## How it works

```
RSS Feeds / Manual Input
        │
        ▼
  NewsParser (keyword + LLM)
        │
        ▼
  SignalArbiter (dedup + contradiction check)
        │
        ▼
  FlowAnalyzer (order book + trade volume confirmation)
        │
        ▼
  OrderManager (ATR-based SL/TP, sandbox execution)
        │
        ▼
  PositionTracker (trailing stop, momentum exit)
```

## Features

- **Hybrid NLP** — keyword matching → regex entity resolution → Gemini LLM fallback
- **Order flow validation** — confirms signals via live order book imbalance + volume spike
- **Risk management** — ATR-based stop-loss/take-profit, trailing stop, time limit, position sizing
- **Multi-page Rich TUI** — live signals, open positions, charts, trade history
- **Backtesting** — replay historical signals on 1-min candles with full SL/TP/trailing simulation
- **Resilience** — auto-reconnect on gRPC stream drops, exponential backoff, rate limiting
- **Trade persistence** — closed positions saved to SQLite, survive restarts

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in TOKEN_TINKOFF
python setup_check.py         # verify everything is configured
python main.py                # launch dashboard
```

See **[SETUP.md](SETUP.md)** for detailed setup instructions.

## Usage

```bash
python main.py                          # Full dashboard + RSS feeds
python main.py --no-rss                 # Dashboard only (manual signals)
python main.py --backtest signals.csv   # Backtest on historical signals
pytest tests/ -v                        # Run test suite (409 tests)
```

### Backtest signal format

```csv
ticker,direction,timestamp
SBER,bullish,2026-03-15T10:30:00
GAZP,bearish,2026-03-15T11:00:00
```

## Configuration

All settings live in `.env` (copy from `.env.example`):

| Variable | Required | Description |
|----------|----------|-------------|
| `TOKEN_TINKOFF` | yes | Tinkoff Invest sandbox token |
| `GEMINI_API_KEY` | no | Google Gemini API key (LLM fallback) |
| `USE_SANDBOX` | no | `true` (default) / `false` for production |

Live parameters can be changed without restart from the dashboard:

```
set trailing_stop_pct 2.0
set min_confidence 0.45
set monitor_interval_sec 3.0
```

## Project structure

```
main.py                 Entry point
dashboard.py            Rich TUI (3 pages)
setup_check.py          Setup validation script
bot/
  core.py               Orchestrator
  config.py             Config with validation
  models.py             Dataclasses
  signal_arbiter.py     Dedup + contradiction detection
  listeners/            News sources (RSS, Telegram)
  validators/           Order flow analysis
  execution/            Orders, risk, position tracking
  utils/                Logging, retry, rate limiting, SQLite
backtest/               Backtesting module
data/                   Whitelists, keywords, RSS feeds
tests/                  409 pytest tests (~56% coverage)
```

## Safety

- Always runs in **sandbox mode** by default — no real money
- Never commit `.env` (it's in `.gitignore`)
- Set `USE_SANDBOX=false` only when ready for production

## Requirements

- Python 3.10+
- Tinkoff Invest account with sandbox token
- Google Gemini API key (optional, free tier)
