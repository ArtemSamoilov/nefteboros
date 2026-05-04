"""LLM-адаптеры под доступные провайдеры.

Будет содержать:
  - gigachat.py  — обёртка над `gigachat` SDK с Минцифры CA
  - cloudru.py   — OpenAI-compatible клиент для Cloud.ru Foundation Models
  - router.py    — выбор провайдера по env / per-call override
  - schema.py    — общие LLMRequest/LLMResponse, нормализация tool-calls

Все адаптеры реализуют LangChain Chat Model interface (BaseChatModel),
чтобы LangGraph мог их вызывать прозрачно.

См. docs/adr/0007-llm-providers.md (TBD).
"""
