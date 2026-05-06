"""nefteboros — AI-агент «Старший аналитик нефтегазового рынка».

Доменный пакет поверх форка Ouroboros (см. docs/adr/0001-fork-ouroboros.md).

Подпакеты:
  - rag       — RAG-пайплайн по отраслевым отчётам (BGE-M3 + ChromaDB)
  - forecast  — прогноз цен нефти и газа (10 активов: Brent/WTI/Urals/ESPO/HH/TTF/JKM/SPIMEX/CBR;
                 RW + SARIMAX + XGBoost + ensemble; conformal CI; walk-forward бектест с
                 regime-сегментацией; см. ADR-0012)
  - search    — Brave API + tier-1/tier-2 фильтр источников
  - llm       — адаптеры GigaChat и Cloud.ru Foundation Models
  - graphs    — LangGraph subgraphs (analyst_graph)
  - prompts   — системный промпт аналитика и evaluation
  - citations — anti-hallucination валидатор источников
  - bot       — Telegram-бот на aiogram

Модули реализуются по PR в порядке зависимостей: llm → rag/search/forecast →
graphs → citations → bot.
"""

__version__ = "0.0.1"
