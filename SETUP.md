# Setup Guide

## 1. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate      # macOS / Linux
# .venv\Scripts\activate       # Windows

pip install -r requirements.txt
```

---

## 2. API tokens

Create a `.env` file in the project root:

```bash
cp .env.example .env   # or create manually
```

Open `.env` and fill in the values:

```env
# Required — Tinkoff Invest API token (sandbox or production)
TOKEN_TINKOFF=t.xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Optional — Google Gemini API key (LLM fallback when rules are inconclusive)
# Free tier: Flash / Flash-Lite models only. Get at: https://aistudio.google.com/apikey
GEMINI_API_KEY=AIzaSy...
# GEMINI_MODEL=gemini-2.5-flash-lite

# Optional — sandbox mode (default: true)
# Set to false only for live production trading
USE_SANDBOX=true
```

### Getting the Tinkoff token

1. Open [Tinkoff Invest](https://www.tinkoff.ru/invest/)
2. Go to **Settings → Tokens**
3. Create a **Sandbox** token (no real money, safe for testing)
4. Copy the token value — it starts with `t.`

> ⚠️ Never commit `.env` to git. It is already in `.gitignore`.

---

## 3. Sanity check

Run this before starting the bot to verify everything is configured correctly:

```bash
python setup_check.py
```

Expected output when everything is OK:

```
✅ Python 3.12+
✅ Dependencies installed
✅ .env found
✅ TOKEN_TINKOFF set
✅ Tinkoff API connection (sandbox)
✅ Sandbox account found: xxxxx
✅ Account balance: 1,000,000 RUB
✅ Data files present (whitelist, keywords, entities, sectors, rss_feeds)
✅ Whitelist: 39 instruments
⚠️  Gemini: region blocked or no free tier — LLM fallback off (optional; rules NLP still works)
```

---

## 4. Tests & benchmarks

```bash
pytest tests/ -v                              # ~439 tests (~61% coverage on bot/)
pytest tests/ -v -m "not llm"               # Skip Gemini API tests
python -m bot.eval.nlp_metrics              # NLP rules eval (38 headlines)
python -m bot.eval.nlp_metrics --check-gemini
python -m bot.eval.nlp_metrics --compare      # Rules vs Gemini on hard corpus
```

Reports: [docs/nlp_eval_report.md](docs/nlp_eval_report.md), [docs/backtest_report.md](docs/backtest_report.md).

> GitHub Actions CI may fail outside Russia — Tinkoff SDK (`t-tech-investments`) requires Russian IP. Run tests locally.

---

## 5. Start the bot

```bash
# Full dashboard with RSS feeds
python main.py

# Dashboard without RSS (manual signals only)
python main.py --no-rss

# Run backtest on historical signals
python main.py --backtest signals.csv
```

### Dashboard commands

Once running, type commands in the input bar:

| Command | Description |
|---------|-------------|
| `trade <text>` | Parse news text and execute if signal found |
| `signal SBER bullish` | Manually inject a signal |
| `analyze SBER` | Show current order flow for a ticker |
| `positions` | List open positions |
| `close SBER` | Close a position manually |
| `stats` | Show trading statistics |
| `config` | Show current config values |
| `set trailing_stop_pct 2.0` | Change a config parameter live |
| `quit` | Exit |

---

## 6. Backtest signal format

```csv
ticker,direction,timestamp
SBER,bullish,2026-03-15T10:30:00
GAZP,bearish,2026-03-15T11:00:00
```

```bash
python main.py --backtest data/backtest_signals_sample.csv
```

---

## 7. Key files

| File | Purpose |
|------|---------|
| `.env` | API tokens (you create this) |
| `data/whitelist.json` | Tradeable instruments (ticker → FIGI) |
| `data/keywords.json` | Bullish/bearish keyword triggers |
| `data/rss_feeds.json` | News feed URLs |
| `data/trades.db` | SQLite trade history (auto-created) |
| `logs/` | Daily event logs (auto-created) |
| `data/backtest_cache/` | Cached candles for backtests (auto-created) |
| `data/nlp_eval.json` | NLP benchmark corpus (easy, rules) |
| `data/nlp_eval_llm.json` | NLP benchmark corpus (hard, Gemini compare) |
| `data/backtest_signals_sample.csv` | Sample backtest signals |
| `docs/portfolio_tradingbot.pdf` | Portfolio one-pager (ITMO / project description) |
