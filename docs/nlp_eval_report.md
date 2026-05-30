# NLP evaluation report (rules-only)

**Дата прогона:** 2026-05-30  
**Corpus:** [`data/nlp_eval.json`](../data/nlp_eval.json) — 38 hand-labeled русскоязычных заголовков  
**Режим:** `use_llm_fallback=False` (только rules + словари `data/`)

## Метрики

| Метрика | Значение |
|---------|----------|
| Sentiment accuracy | 100% |
| Sentiment macro-F1 | 1.000 |
| Ticker recall | 100% |
| Ticker precision | 66% |
| False positive rate (negative set, n=5) | 0% |

Ticker precision ниже recall из‑за sector routing: геополитические триггеры возвращают несколько тикеров сектора (ожидаемое поведение).

## Ограничения

- Corpus **не** out-of-sample RSS; заголовки подобраны под текущие словари.
- LLM-fallback в этом прогоне отключён.
- Метрики **не** заменяют live-валидацию на потоке новостей.

## Воспроизведение

```bash
python -m bot.eval.nlp_metrics
pytest tests/test_nlp_eval.py -v
```

## Gemini fallback (optional)

Corpus для «сложных» заголовков: `data/nlp_eval_llm.json`.

```bash
python -m bot.eval.nlp_metrics --check-gemini
python -m bot.eval.nlp_metrics --compare
pytest tests/test_nlp_eval_llm.py -v -m llm
```

Требуется валидный `GEMINI_API_KEY` в `.env`. CI-тесты с маркером `llm` пропускаются без ключа.
