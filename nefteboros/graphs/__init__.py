"""LangGraph subgraphs.

Будет содержать:
  - analyst_graph.py — основной граф маршрутизации запроса:
      classify_intent → route → {rag | web | forecast} → synthesize → validate_citations
  - state.py         — TypedDict состояний графа
  - nodes/           — отдельные узлы (для читаемости и юнит-тестов)

Граф вызывается из tool'а `analyst_query` (зарегистрирован skill'ом neftegaz_analyst).
LangGraph живёт ВНУТРИ одного tool'а Ouroboros, не заменяет его loop.

См. docs/adr/0005-langgraph-as-subgraph.md (TBD).
"""
