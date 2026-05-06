# Архитектура Event-Driven Trading Bot

Техническая документация для разработчиков.

---

## Оглавление

1. [Обзор архитектуры](#обзор-архитектуры)
2. [Модули](#модули)
   - [bot/models.py](#botmodelspy)
   - [bot/config.py](#botconfigpy)
   - [bot/signal_arbiter.py](#botsignal_arbiterpy)
   - [bot/listeners/](#botlisteners)
   - [bot/validators/](#botvalidators)
   - [bot/execution/](#botexecution)
   - [bot/core.py](#botcorepy)
   - [bot/utils/event_logger.py](#botutilsevent_loggerpy)
3. [Dashboard](#dashboard)
4. [Поток данных](#поток-данных)
5. [API T-Invest](#api-t-invest)
6. [Расширение функционала](#расширение-функционала)

---

## Обзор архитектуры

```
┌─────────────────────────────────────────────────────────────┐
│              dashboard.py (Rich Live TUI)                    │
│  Page 1: Signals/Positions/Log                               │
│  Page 2: Charts (plotext)                                    │
│  Page 3: Trade History                                       │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                   TradingBot (core.py)                        │
│  - Оркестрация всех модулей                                  │
│  - SignalArbiter gate (dedup + contradiction)                │
│  - Unified exit handling (_handle_exit → _attempt_reversal)  │
└──┬──────────────┬───────────────────┬──────────────┬────────┘
   │              │                   │              │
   ▼              ▼                   ▼              ▼
┌─────────┐ ┌──────────────┐ ┌─────────────┐ ┌──────────────┐
│Listeners│ │ FlowAnalyzer │ │OrderManager │ │PositionTracker│
│(news)   │ │ (validators) │ │ (execution) │ │  (execution)  │
└────┬────┘ └──────┬───────┘ └──────┬──────┘ └──────┬───────┘
     │             │                │                │
     ▼             ▼                ▼                ▼
┌─────────┐ ┌──────────────┐ ┌───────────┐  ┌──────────────┐
│RSS Feed │ │ OrderBook    │ │RiskManager│  │ T-Invest API │
│Telegram │ │ TradesStream │ │           │  │              │
│CLI      │ └──────────────┘ └───────────┘  └──────────────┘
└─────────┘
```

---

## Модули

### bot/models.py

Датаклассы и перечисления.

#### Sentiment (Enum)
```python
class Sentiment(Enum):
    BULLISH = "bullish"    # Ожидаем рост → BUY
    BEARISH = "bearish"    # Ожидаем падение → SELL
    NEUTRAL = "neutral"    # Нет сигнала → NO ACTION
```

#### PositionState (Enum)
```python
class PositionState(Enum):
    ACTIVE = "active"        # Позиция открыта и мониторится
    REVERSING = "reversing"  # Идёт попытка разворота (guard от race condition)
    CLOSED = "closed"        # Позиция закрыта
```

#### NewsEvent
```python
@dataclass
class NewsEvent:
    ticker: str           # 'SBER'
    figi: str             # 'BBG004730N88'
    sentiment: Sentiment
    confidence: float     # 0.0 - 1.0
    keywords_found: list[str]
    timestamp: datetime
    source: str           # 'telegram', 'manual', 'rss', 'cli'
    raw_text: Optional[str]
```

#### Position
```python
@dataclass
class Position:
    figi: str
    ticker: str
    direction: Sentiment
    entry_price: float
    quantity: int
    entry_time: datetime
    stop_loss: float
    take_profit: Optional[float]
    atr: float

    # State tracking
    peak_price: float           # Инициализируется = entry_price
    state: PositionState        # ACTIVE → REVERSING → CLOSED
    is_open: bool = True
    exit_price: Optional[float]
    exit_time: Optional[datetime]
    exit_reason: Optional[str]
    pnl: Optional[float]
```

#### ExitSignal
```python
@dataclass
class ExitSignal:
    figi: str
    ticker: str
    reason: str               # 'TRAILING_STOP', 'MOMENTUM_REVERSAL', 'SL', 'TP', 'TIME'
    exit_price: float
    should_reverse: bool      # True → attempt reversal via FlowAnalyzer
    direction: Sentiment      # Direction of the closed position
```

#### ReversalContext
```python
@dataclass
class ReversalContext:
    figi: str
    ticker: str
    original_direction: Sentiment  # Direction being reversed FROM
    trigger: str                   # What triggered the reversal
```

---

### bot/config.py

Конфигурация из `.env` и `data/*.json`.

```python
@dataclass
class TradingConfig:
    max_position_rub: float = 50000.0
    stop_loss_atr_mult: float = 2.0
    take_profit_atr_mult: float = 3.0
    max_position_time_min: int = 15
    trailing_stop_pct: float = 2.0          # % drop from peak → trailing stop
    momentum_candles: int = 5               # Candles to check for momentum
    momentum_bearish_ratio: float = 0.7     # Threshold for bearish momentum
    momentum_check_interval_sec: float = 30
    dedup_window_sec: float = 300           # Signal dedup window (5 min)

@dataclass
class ValidationConfig:
    volume_spike_threshold: float = 3.0
    imbalance_threshold: float = 0.3
    price_move_threshold: float = 0.1
    reaction_window_sec: float = 3.0
    min_confidence: float = 0.6
```

---

### bot/signal_arbiter.py

Дедупликация сигналов и обнаружение противоречий.

```python
class ArbiterDecision(Enum):
    PROCEED = "proceed"             # Новый сигнал, обрабатываем
    DUPLICATE = "duplicate"         # Уже обработан в пределах окна
    CONTRADICTION = "contradiction" # Противоречит открытой ACTIVE позиции

class SignalArbiter:
    def evaluate(event: NewsEvent, positions: dict) -> ArbiterAction
        # 1. Prune expired signals
        # 2. Check duplicates (same ticker + sentiment within window)
        # 3. Check contradictions with ACTIVE positions
        # 4. Return ArbiterAction with decision + optional existing_position
```

---

### bot/listeners/

#### NewsParser (`news_parser.py`)

Многослойный NLP парсер:
1. **Keyword matching** — быстрый первый проход по `data/keywords.json`
2. **Entity resolution** — `data/entities.json` (синонимы, regex, сленг)
3. **Sector mapping** — `data/sectors.json` (макро-новости без явного тикера)
4. **LLM fallback** — Google Gemini API для сложных случаев

```python
class NewsParser:
    def parse(text: str, source: str) -> ParseResult
    def to_news_events(result: ParseResult, source: str) -> list[NewsEvent]
```

#### RSSListener (`rss_listener.py`)

Async RSS poller.

```python
class RSSListener:
    def __init__(feeds_config_path, logger)
    def on_entry(callback)       # Регистрация callback для новых записей
    async def start()            # Бесконечный цикл polling
    def stop()
```

#### TelegramListener (`telegram_listener.py`)

Telethon userbot (деактивирован из-за рисков бана).

---

### bot/validators/

#### FlowAnalyzer (`flow_analyzer.py`)

Комбинирует Order Book и Trade Stream для валидации.

```python
class FlowAnalyzer:
    async def analyze(figi: str) -> FlowAnalysis
        """Анализ текущего состояния (без ожидания)."""

    async def validate(event: NewsEvent) -> FlowAnalysis
        """Валидация с ожиданием подтверждения (timeout)."""

    async def validate_reversal(ctx: ReversalContext) -> FlowAnalysis
        """Валидация разворота (без синтетического NewsEvent)."""
```

**Логика валидации:**
```
1. Получаем метрики: imbalance, volume_ratio, price_change
2. Определяем sentiment из каждого источника (OB, trades, price)
3. Голосование: ≥2 голоса = detected_sentiment
4. Confidence = f(imbalance, volume, price, agreement)
5. CONFIRMED если confidence ≥ min и detected == expected
```

#### OrderBookStream / TradesStream

Стримы данных через T-Invest gRPC.

---

### bot/execution/

#### RiskManager (`risk_manager.py`)

Чистые расчёты (без мутации состояния).

```python
class RiskManager:
    async def calculate_atr(figi, period=14) -> float
    def calculate_position_size(price, lot_size, max_rub) -> int
    def calculate_stop_loss(entry_price, direction, atr, mult) -> float
    def calculate_take_profit(entry_price, direction, atr, mult) -> float

    # Exit checks (pure functions)
    def check_stop_loss(position, current_price) -> bool
    def check_take_profit(position, current_price) -> bool
    def check_time_limit(position) -> bool
    def check_trailing_stop(position, current_price) -> bool
    async def check_momentum(figi) -> Sentiment
```

#### PositionTracker (`position_tracker.py`)

Владеет состоянием позиций. Генерирует `ExitSignal`.

```python
class PositionTracker:
    def add_position(position: Position)
    def get_position(figi) -> Position
    def get_all_positions() -> list[Position]
    async def close_position(figi, exit_price, reason) -> Position

    async def monitor_loop(interval_sec=0.5)
        """
        Для каждой ACTIVE позиции:
        1. SL/TP/Time → close (ExitSignal, should_reverse=False)
        2. Update peak_price
        3. Trailing stop → ExitSignal(should_reverse=True)
        4. Momentum check → ExitSignal(should_reverse=True)
        """

    def on_exit(callback)   # Single unified exit callback
    def get_stats() -> dict
    def get_history(limit) -> list[Position]
```

#### OrderManager (`order_manager.py`)

```python
class OrderManager:
    async def execute_signal(event: NewsEvent, analysis: FlowAnalysis) -> Position
    async def close_position_market(figi, reason) -> bool
```

---

### bot/core.py

Главный оркестратор.

```python
class TradingBot:
    async def start()
    async def stop()

    async def process_event(event: NewsEvent)
        """
        1. SignalArbiter.evaluate()
           - DUPLICATE → log & skip
           - CONTRADICTION → _attempt_reversal() + continue
           - PROCEED → continue
        2. FlowAnalyzer.validate()
        3. OrderManager.execute_signal() if CONFIRMED
        """

    async def _handle_exit(signal: ExitSignal)
        """Unified exit callback from PositionTracker."""
        # should_reverse=False → log
        # should_reverse=True → _attempt_reversal()

    async def _attempt_reversal(ctx: ReversalContext)
        """
        1. Set position.state = REVERSING (guard)
        2. Close original position
        3. FlowAnalyzer.validate_reversal(ctx)
        4. If confirmed → open reverse position
        """
```

---

### bot/utils/event_logger.py

Структурированное логирование с подписчиками.

```python
class EventType(Enum):
    NEWS_PARSED, NEWS_IGNORED,
    SIGNAL_VALIDATED, SIGNAL_REJECTED, SIGNAL_INCONCLUSIVE,
    SIGNAL_DUPLICATE, SIGNAL_CONTRADICTION,
    ORDER_PLACED, ORDER_FAILED,
    POSITION_OPENED, POSITION_CLOSED,
    STOP_LOSS_HIT, TAKE_PROFIT_HIT, TIME_LIMIT_HIT,
    TRAILING_STOP_HIT, MOMENTUM_EXIT, POSITION_REVERSED,
    ERROR

class EventLogger:
    def subscribe(callback)       # Dashboard подписывается на события
    def log(event_type, **data)   # JSON + console (если включён)
    # Convenience: .news_parsed(), .position_opened(), .position_closed(), etc.
```

---

## Dashboard

`dashboard.py` — Rich Live TUI с 3 страницами.

Переключение: `1`/`2`/`3` или `Tab`.

### Техническая реализация
- `tty.setcbreak()` для захвата клавиш без echo (output processing сохранён)
- `Rich.Live` с `screen=False` (совместимо с IDE терминалами)
- Фоновый `threading.Thread` для чтения stdin
- PnL snapshots каждые 30 сек для графиков
- Signal timeline tracking для bar chart

### Страницы
1. **DASHBOARD**: signals table, positions table, event log, input
2. **CHARTS**: plotext signal timeline, cumulative PnL, signal breakdown
3. **HISTORY**: detailed closed trades table

---

## Поток данных

```
1. ВХОД: NewsEvent
   ├── source: 'cli' (ручной ввод)
   ├── source: 'rss:feed_name' (RSS)
   └── source: 'telegram' (Telethon, деактивирован)
   │
   ▼
2. АРБИТРАЖ: SignalArbiter.evaluate()
   ├── DUPLICATE → log SIGNAL_DUPLICATE, skip
   ├── CONTRADICTION → _attempt_reversal() для существующей позиции
   └── PROCEED ↓
   │
   ▼
3. ВАЛИДАЦИЯ: FlowAnalyzer.validate()
   ├── OrderBookStream → imbalance
   ├── TradesStream → volume_ratio, price_change
   └── Голосование → detected_sentiment, confidence
   │
   ├── CONFIRMED → продолжаем
   ├── REJECTED → log SIGNAL_REJECTED, stop
   └── INCONCLUSIVE → log SIGNAL_INCONCLUSIVE, stop
   │
   ▼
4. ИСПОЛНЕНИЕ: OrderManager.execute_signal()
   ├── RiskManager.calculate_atr()
   ├── RiskManager.calculate_position_size()
   ├── Marketable Limit Order → T-Invest API
   └── PositionTracker.add_position()
   │
   ▼
5. МОНИТОРИНГ: PositionTracker.monitor_loop()
   ├── check_stop_loss() → ExitSignal(should_reverse=False)
   ├── check_take_profit() → ExitSignal(should_reverse=False)
   ├── check_time_limit() → ExitSignal(should_reverse=False)
   ├── check_trailing_stop() → ExitSignal(should_reverse=True)
   └── check_momentum() → ExitSignal(should_reverse=True)
   │
   ▼
6. ВЫХОД: core._handle_exit(signal)
   ├── should_reverse=False → close & log
   └── should_reverse=True → _attempt_reversal()
       ├── PositionState → REVERSING (guard)
       ├── Close original position
       ├── FlowAnalyzer.validate_reversal()
       └── Open reverse position if CONFIRMED
```

---

## API T-Invest

Используемые методы SDK `t-tech-investments`.

### Market Data
```python
client.market_data.get_order_book(figi, depth=20)
client.market_data.get_last_prices(figi=[figi])
client.market_data.get_candles(figi, from_, to, interval)
client.market_data.get_last_trades(figi, from_, to)
```

### Orders (Sandbox)
```python
client.sandbox.post_sandbox_order(
    account_id, figi, quantity, price=Quotation,
    direction=OrderDirection, order_type=OrderType, order_id=str(uuid4())
)
client.sandbox.cancel_sandbox_order(account_id, order_id)
```

### Типы данных
```python
from t_tech.invest import (
    Client, Quotation, MoneyValue,
    OrderDirection,   # ORDER_DIRECTION_BUY / SELL
    OrderType,        # ORDER_TYPE_LIMIT / MARKET
    CandleInterval,   # CANDLE_INTERVAL_1_MIN, _5_MIN, etc.
)
```

---

## Расширение функционала

### Новый источник сигналов

1. Создать `bot/listeners/my_source.py`
2. Реализовать async listener с callback
3. Интегрировать в `TradingBot.start()` через `asyncio.create_task()`
4. Парсить данные через `NewsParser` или создавать `NewsEvent` напрямую

### Новая метрика Order Flow

1. Добавить в `FlowAnalysis.details`
2. Учесть в `FlowAnalyzer._calculate_confidence()`
3. Добавить голос в `_combine_sentiments()`

### Новое условие выхода

1. Добавить check-метод в `RiskManager` (pure function)
2. Добавить вызов в `PositionTracker.monitor_loop()`
3. Генерировать `ExitSignal` с правильным `should_reverse`

### Новая страница Dashboard

1. Создать `_build_my_page(state) -> Panel` в `dashboard.py`
2. Добавить имя в `PAGE_NAMES`
3. Добавить ветку в `build_layout()` для нового `current_page`

---

## Файлы конфигурации

### .env
```
TOKEN_TINKOFF=t.xxx...
USE_SANDBOX=true
GEMINI_API_KEY=xxx...          # Опционально
TELEGRAM_API_ID=12345          # Опционально
TELEGRAM_API_HASH=abc...       # Опционально
```

### data/whitelist.json
```json
{"instruments": [{"ticker": "SBER", "figi": "BBG004730N88", "name": "Сбер"}, ...]}
```

### data/keywords.json
```json
{"bullish": {"keywords": ["дивиденд", "байбэк", ...], "weight": 1.0}, ...}
```

### data/entities.json
```json
{"ticker_synonyms": {"Сбер": "SBER", ...}, "regex_patterns": [...]}
```

### data/sectors.json
```json
{"oil_gas": ["ROSN", "LKOH", "GAZP", ...], "banking": ["SBER", "VTBR", ...]}
```

### data/rss_feeds.json
```json
[{"name": "SmartLab", "url": "...", "interval_sec": 60}, ...]
```
