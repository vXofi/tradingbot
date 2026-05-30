# Backtest report (sample)

**Дата прогона:** 2026-05-30  
**Файл сигналов:** [`data/backtest_signals_sample.csv`](../data/backtest_signals_sample.csv) (20 сигналов, янв.–февр. 2026, будние дни MOEX)  
**Режим:** walk-forward симуляция на 1-минутных свечах Tinkoff API; order-flow validation **не** моделируется — все сигналы считаются подтверждёнными.

## Параметры симуляции

- SL = 2.0×ATR, TP = 3.0×ATR, trailing stop 1.5%, time limit 15 мин
- Quantity = 1 лот на сделку (упрощение; без комиссий и проскальзывания)
- PnL в **относительных пунктах цены × quantity**, не реальный рублёвый результат портфеля

## Сводка

| Метрика | Значение |
|---------|----------|
| Сделок | 20 |
| Wins / Losses | 8 / 12 |
| Win rate | 40.0% |
| Total PnL | +7.65 (sim units) |
| Avg PnL | +0.38 |
| Best trade | +4.29 |
| Worst trade | −1.31 |
| Max drawdown | 1.68 |
| Profit factor | 2.97 |
| Avg duration | 8.9 min |

## Выходы по причинам

| Reason | Count | PnL | Win rate |
|--------|-------|-----|----------|
| STOP_LOSS | 10 | −3.02 | 0% |
| TAKE_PROFIT | 1 | +4.29 | 100% |
| TIME_LIMIT | 9 | +6.39 | 78% |

## Ограничения (обязательно для интерпретации)

1. **Не production PnL** — симуляция на исторических свечах; real trading не проводился.
2. Сигналы в CSV — **синтетический набор** для демонстрации pipeline backtest, не размеченный корpus новостей.
3. Без комиссий, проскальзывания, частичного исполнения и order-flow фильтрации.
4. Результат **не** доказывает инвестиционную привлекательность стратегии.

## Воспроизведение

```bash
python main.py --backtest data/backtest_signals_sample.csv
```

Кэш свечей: `data/backtest_cache/` (создаётся автоматически при первом прогоне).
