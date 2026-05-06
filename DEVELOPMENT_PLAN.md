# Event-Driven Trading Bot — План разработки

## Статус реализации

| # | Этап | Статус | Файлы |
|---|------|--------|-------|
| 1 | Инфраструктура (config, models, whitelist) | ✅ Готово | `bot/config.py`, `bot/models.py`, `data/whitelist.json` |
| 2 | Ticker mapping + entity resolution | ✅ Готово | `data/entities.json`, `data/sectors.json`, `data/keywords.json` |
| 3 | News Parser (hybrid keyword + LLM) | ✅ Готово | `bot/listeners/news_parser.py` |
| 4 | RSS Listener | ✅ Готово | `bot/listeners/rss_listener.py`, `data/rss_feeds.json` |
| 5 | Telegram Listener | ✅ Готово (не активен) | `bot/listeners/telegram_listener.py` |
| 6 | Order Book + Trades Stream | ✅ Готово | `bot/validators/orderbook_stream.py`, `trades_stream.py` |
| 7 | Flow Analyzer | ✅ Готово | `bot/validators/flow_analyzer.py` |
| 8 | Order Manager | ✅ Готово | `bot/execution/order_manager.py` |
| 9 | Risk Manager (ATR, SL/TP, trailing stop, momentum) | ✅ Готово | `bot/execution/risk_manager.py` |
| 10 | Position Tracker (unified exit, state machine) | ✅ Готово | `bot/execution/position_tracker.py` |
| 11 | Signal Arbiter (dedup + contradiction) | ✅ Готово | `bot/signal_arbiter.py` |
| 12 | Core Orchestrator (reversals, exit handling) | ✅ Готово | `bot/core.py` |
| 13 | Event Logger | ✅ Готово | `bot/utils/event_logger.py` |
| 14 | Dashboard (multi-page, charts, controls) | ✅ Готово | `dashboard.py` |
| 15 | Единая точка входа | ✅ Готово | `main.py` |
| 16 | Retry / reconnection / rate limiting | ✅ Готово | `bot/utils/retry.py`, модификации stream/order/parser |
| 17 | Автоматические тесты (pytest) | ✅ Готово (381 тест, 56% покрытие) | `tests/`, `pyproject.toml` |
| 18 | Валидация конфигурации | ✅ Готово | `bot/config.py` (__post_init__) |
| 19 | Персистенция истории сделок (SQLite) | ✅ Готово | `bot/utils/trade_db.py`, `data/trades.db` |
| 20 | Graceful shutdown (SIGTERM, close-all) | ✅ Готово | `dashboard.py`, `bot/core.py` |
| 21 | API rate limiting (async) | ✅ Готово | `bot/utils/retry.py` (AsyncRateLimiter), monitor 2s |
| 22 | Setup guide + sanity check | ✅ Готово | `SETUP.md`, `.env.example`, `setup_check.py` |

---

## Архитектура проекта

```
tradingbot/
├── main.py                        # Единая точка входа
├── dashboard.py                   # Rich Live TUI (3 страницы)
├── _sandbox_debug.py              # Отладочный скрипт (legacy)
├── cli.py                         # CLI утилита
│
├── bot/
│   ├── __init__.py
│   ├── core.py                    # Оркестратор: arbiter → validate → execute → monitor
│   ├── config.py                  # Config, TradingConfig, ValidationConfig
│   ├── models.py                  # Sentiment, NewsEvent, Position, ExitSignal, etc.
│   ├── signal_arbiter.py          # Дедупликация + обнаружение противоречий
│   │
│   ├── listeners/                 # Модуль "СЛУХ"
│   │   ├── news_parser.py         # Hybrid NLP: keywords → regex → LLM fallback
│   │   ├── rss_listener.py        # Async RSS (основной источник новостей)
│   │   └── telegram_listener.py   # Telethon userbot (деактивирован)
│   │
│   ├── validators/                # Модуль "ЗРЕНИЕ"
│   │   ├── flow_analyzer.py       # Комбинирует OB imbalance + volume + price
│   │   ├── orderbook_stream.py    # Стрим стакана
│   │   └── trades_stream.py       # Лента сделок
│   │
│   ├── execution/                 # Модуль "ИСПОЛНЕНИЕ"
│   │   ├── order_manager.py       # Marketable limit orders
│   │   ├── risk_manager.py        # ATR, SL/TP, trailing stop, momentum
│   │   └── position_tracker.py    # Мониторинг позиций, unified exit
│   │
│   └── utils/
│       └── event_logger.py        # JSON + console логирование с подписчиками
│
├── data/
│   ├── whitelist.json             # ~40 ликвидных акций MOEX
│   ├── keywords.json              # Триггеры: bullish/bearish/geopolitical
│   ├── entities.json              # Синонимы тикеров, regex, сленг
│   ├── sectors.json               # Сектор → тикеры (для макро-новостей)
│   └── rss_feeds.json             # RSS фиды и интервалы
│
├── logs/                          # JSON логи по дням
├── requirements.txt
└── .env
```

---

## Поток данных (текущий)

```
RSS Feed / CLI / Telegram
        │
        ▼
  NewsParser.parse()
  (keywords → regex → Gemini LLM)
        │
        ▼
  SignalArbiter.evaluate()
  ├── DUPLICATE → skip
  ├── CONTRADICTION → _attempt_reversal()
  └── PROCEED ↓
        │
        ▼
  FlowAnalyzer.validate()
  (OB imbalance + volume spike + price direction)
  ├── CONFIRMED → execute
  ├── REJECTED → skip
  └── INCONCLUSIVE → skip
        │
        ▼
  OrderManager.execute_signal()
  (position sizing → marketable limit → register)
        │
        ▼
  PositionTracker.monitor_loop()
  ├── SL/TP/Time → close (no reversal)
  ├── Trailing stop hit → ExitSignal(should_reverse=True)
  └── Momentum reversal → ExitSignal(should_reverse=True)
        │
        ▼
  core._handle_exit()
  ├── should_reverse=False → log & done
  └── should_reverse=True → _attempt_reversal()
      ├── close original position
      ├── FlowAnalyzer.validate_reversal()
      └── open reverse position if confirmed
```

---

## Dashboard (3 страницы)

Переключение: клавиши `1`/`2`/`3` или `Tab`

### Страница 1: DASHBOARD
- Header (режим, uptime, статистика)
- LIVE SIGNALS (последние сигналы с тикерами, направлением, методом)
- OPEN POSITIONS (тикер, сторона, цена входа, peak, SL/TP, состояние)
- EVENT LOG (все события бота)
- INPUT (команды)

### Страница 2: CHARTS (plotext)
- Временная шкала сигналов (bar chart)
- Кумулятивный PnL (line chart)
- Разбивка сигналов (bullish/bearish/neutral)

### Страница 3: HISTORY
- Полная таблица закрытых сделок (вход, выход, PnL, причина, длительность)

### Команды
```
news <text>         — парсить текст (без исполнения)
trade <text>        — парсить + исполнить
signal <T> <dir>    — ручной сигнал
analyze <T>         — анализ order flow
close <T>           — закрыть позицию
closeall            — закрыть все позиции
positions           — список открытых позиций
stats               — статистика торговли
history [n]         — история сделок
config              — текущие параметры
set <param> <val>   — изменить параметр
rss on/off          — управление RSS
page 1/2/3          — переключить страницу
clear               — очистить буферы
quit                — выход
```

---

## Ключевые решения

### Источники новостей
- **RSS** — основной источник (безопасно, не требует аккаунта)
- **Telegram (Telethon)** — реализован, но деактивирован из-за рисков бана
- **CLI** — ручной ввод для тестирования

### NLP стратегия
- **Слой 1:** Быстрый keyword matching (0ms latency)
- **Слой 2:** Regex entity extraction из `entities.json`
- **Слой 3:** LLM fallback (Gemini API) для сложных случаев
- Политические/макро новости через `sectors.json` (нет явного тикера → влияет на сектор)

### Signal Arbiter
- Дедупликация: один тикер+sentiment в пределах `dedup_window_sec`
- Противоречие: если новый сигнал противоречит ACTIVE позиции → попытка разворота

### Position State Machine
```
ACTIVE → REVERSING → CLOSED
  │                    ↑
  └────────────────────┘ (SL/TP/Time — no reversal)
```

### Unified Exit
Все условия выхода генерируют `ExitSignal`:
- `should_reverse=False`: SL, TP, Time limit
- `should_reverse=True`: Trailing stop, Momentum reversal, Signal contradiction

---

## Зависимости

```
python-dotenv>=1.0.0          # .env
t-tech-investments>=0.3.3     # Tinkoff API
telethon>=1.34.0              # Telegram userbot
pandas>=2.0.0                 # Data processing
numpy>=1.24.0                 # Calculations
aiofiles>=23.0.0              # Async file I/O
aiohttp>=3.9.0                # Async HTTP
feedparser>=6.0.0             # RSS parsing
google-generativeai>=0.3.0    # Gemini LLM fallback
rich>=13.0.0                  # Terminal dashboard
plotext>=5.3.0                # Terminal charts
pytest>=7.0.0                 # Тесты
pytest-asyncio>=0.21.0        # Async тесты
pytest-cov>=4.0.0             # Покрытие
```

---

## Известные проблемы и TODO

Аудит кодовой базы (2026-04-10). Приоритет: CRITICAL > HIGH > MEDIUM > LOW.

### ~~CRITICAL: Отсутствие reconnection / retry~~ → Исправлено (этап 16)

### ~~CRITICAL: Отсутствие автоматических тестов~~ → Исправлено (этап 17, 237 тестов)

---

### ~~HIGH-1: История сделок теряется при перезапуске~~ → Исправлено (этап 19)

**Файлы:** `bot/execution/position_tracker.py`

`self._history: list[Position]` хранится только в памяти. При перезапуске бота вся история закрытых позиций теряется. Открытые позиции восстанавливаются через `_reconcile_positions()` (из брокера), но PnL, причины выхода и длительность — нет.

**Последствия:** Невозможно оценить эффективность стратегии за период больше одной сессии. Dashboard страница HISTORY пуста после рестарта.

**Решение:** Персистировать закрытые позиции в SQLite (`positions.db`). Схема: figi, ticker, direction, entry_time, exit_time, entry_price, exit_price, pnl, exit_reason, atr, quantity. Загружать при старте для dashboard.

---

### ~~HIGH-2: Нет валидации конфигурации~~ → Исправлено (этап 18)

**Файлы:** `bot/config.py`

`TradingConfig` и `ValidationConfig` — dataclass без `__post_init__` проверок. Некорректные значения (отрицательный `stop_loss_atr_mult`, нулевой `max_position_rub`, `imbalance_threshold > 1.0`) молча пропускаются в расчёты.

`GEMINI_API_KEY` не проверяется до первого вызова LLM — ошибка появляется только когда keyword layers не справились и нужен fallback.

**Решение:** Добавить `__post_init__` с проверками диапазонов. Валидировать API ключи при старте (dry-run вызов или проверка формата). Альтернативно — мигрировать на Pydantic.

---

### ~~HIGH-3: Неполный graceful shutdown~~ → Исправлено (этап 20)

**Файлы:** `main.py`, `dashboard.py`, `bot/core.py`

- Нет обработки `SIGTERM` (только `KeyboardInterrupt`). Бот не может корректно завершиться из systemd/Docker.
- Открытые позиции **не закрываются** при выходе — бот просто отключается от API.
- `core.stop()` вызывает `position_tracker.stop_monitoring()` и закрывает клиент, но не делает close-all.

**Решение:** Добавить `signal.signal(SIGTERM, handler)` в `main.py`. В `core.stop()` опционально вызывать `close_all_positions()` (с флагом `close_on_exit`). Graceful shutdown: stop RSS → stop monitoring → close positions → close client.

---

### ~~HIGH-4: Нет rate limiting для Tinkoff API~~ → Исправлено (этап 21)

**Файлы:** `bot/validators/flow_analyzer.py`, `bot/execution/order_manager.py`

`_TRADE_FETCH_COOLDOWN = 5.0` — единственный механизм ограничения запросов к Tinkoff. При одновременной валидации нескольких тикеров, `poll()` и `get_last_price()` вызываются параллельно без общего лимитера.

T-Invest SDK имеет внутренние лимиты, но при их превышении — `RequestError` без автоматического backoff на уровне бота.

**Решение:** Добавить общий `asyncio.Semaphore` или token-bucket для всех Tinkoff API вызовов. Вынести `_TRADE_FETCH_COOLDOWN` в `ValidationConfig`.

---

### MEDIUM-1: Отсутствие внешних уведомлений

**Файлы:** нет (новый модуль)

Dashboard — единственный способ наблюдения. Нет Telegram-уведомлений о сделках, нет алертов при срабатывании SL, нет оповещения о падении бота.

**Решение:** Создать `bot/utils/alerter.py` с интерфейсом `Alerter.send(event)`. Реализации: `TelegramAlerter` (через Bot API, не Telethon), `WebhookAlerter`. Подписать на `EventLogger` через существующий механизм подписчиков. Фильтр: уведомлять только о `POSITION_OPENED`, `POSITION_CLOSED`, `STOP_LOSS_HIT`, `ERROR`.

---

### MEDIUM-2: Отсутствие бэктестинга

**Файлы:** нет (новый модуль)

Стратегию можно оценить только в реальном времени (sandbox). Нет возможности прогнать на исторических данных для оценки win rate, max drawdown, Sharpe ratio.

**Решение:** Создать `backtest/` модуль. Загрузка исторических свечей через `client.market_data.get_candles()` (или CSV). Replay через FlowAnalyzer с мок-данными. Вывод: equity curve, win rate, PnL distribution, max drawdown.

---

### MEDIUM-3: Race condition в position_tracker

**Файлы:** `bot/execution/position_tracker.py`

`self._positions` (dict) не защищён lock-ом. `add_position()` и `monitor_loop()` могут конфликтовать:
- `monitor_loop` делает `list(self._positions.keys())` (snapshot), но позиция может быть удалена между snapshot и доступом.
- Работает сейчас, потому что asyncio однопоточный, но НЕ безопасно при `concurrent.futures` или multi-threaded callers.

**Решение:** Добавить `asyncio.Lock` вокруг мутаций `_positions`. Или явно документировать, что `PositionTracker` — single-threaded only.

---

### LOW-1: Нет ротации логов

**Файлы:** `bot/utils/event_logger.py`

JSONL файлы в `logs/` накапливаются бесконечно (один файл в день). За 3+ месяца — десятки мегабайт неуправляемых файлов.

**Решение:** Добавить ротацию: удалять файлы старше N дней (по умолчанию 30). Опционально gzip для архивных. Вызывать при старте бота.

---

### LOW-2: Расширить покрытие тестов

**Текущее покрытие:** 43% (237 тестов). Не покрыты:
- `bot/core.py` (0%) — требует мок всего pipeline
- `bot/execution/position_tracker.py` (19%) — требует мок API для мониторинга
- `bot/listeners/rss_listener.py` (22%) — требует мок aiohttp
- `bot/validators/` (0%) — требует мок gRPC streams

**Решение:** Добавить `tests/integration/` с моками для полного pipeline. Приоритет: `position_tracker` (state machine), `core.py` (orchestration logic).

---

### LOW-3: `datetime.utcnow()` deprecated warnings

**Файлы:** `bot/execution/risk_manager.py` (строки 79, 80, 357, 358)

Python 3.12+ выдаёт `DeprecationWarning` для `datetime.utcnow()`. Будет удалён в Python 3.14.

**Решение:** Заменить на `datetime.now(datetime.UTC)`.
