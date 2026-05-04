"""LLM-адаптеры под доступные провайдеры.

Все адаптеры возвращают объекты, совместимые с LangChain `BaseChatModel`,
чтобы LangGraph subgraph (`nefteboros/graphs/`) мог использовать их прозрачно
(invoke, stream, bind_tools и т.д.).

Модули:
  - hydra.py     — HydraGPT (`https://hydragpt.ru/v1`, OpenAI-совместимый шлюз
                   к моделям Cloud.ru/JOI: kimi-k2p6, glm-5p1, deepseek-v4-pro
                   и др.). Реализован через `langchain_openai.ChatOpenAI` с
                   подменённым `base_url`.
  - gigachat.py  — GigaChat (Sber, Lite/Pro/Max/Ultra). Реализован через
                   `langchain_gigachat.GigaChat`.
  - router.py    — фабрика `get_chat_model()` — выбор провайдера/модели через
                   env (`PRIMARY_LLM_PROVIDER`, `PRIMARY_LLM_MODEL`) или per-call.

Сравнительные метрики моделей собирает `scripts/eval/eval_llm.py` (см.
docs/experiments/llm-comparison.md). Архитектурное решение по выбору двух
провайдеров — docs/adr/0007-llm-providers.md.
"""

from nefteboros.llm.router import get_chat_model

__all__ = ["get_chat_model"]
