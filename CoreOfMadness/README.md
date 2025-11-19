# CoreOfMadness — Ядро Безумия

Двухрежимный LLM-движок:
- LM Studio proxy (OpenAI API совместимый).
- Transformers + AutoGPTQ (локальная загрузка GPT-NeoX-20B в 4-бит — требовательна к RAM).

## Быстрый старт (LM Studio)
1) В LM Studio включи Local server (OpenAI compatible) на http://127.0.0.1:1234 и открой модель 20B.
2) Запуск:
   ./scripts/run_server.sh
3) Проверка:
   ./scripts/smoke_test.sh

## Переключение на transformers
В `configs/default.yaml`:
engine.backend: transformers
engine.model_id: EleutherAI/gpt-neox-20b
engine.quantization: gptq  # или none
