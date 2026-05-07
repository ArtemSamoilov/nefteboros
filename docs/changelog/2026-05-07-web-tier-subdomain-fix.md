# 2026-05-07 — Subdomain-aware tier matching + минимальное расширение blacklist

**PR:** `fix/web-tier-subdomain-match`
**Связано:** [ADR-0022](../adr/0022-web-search-brave.md) (web-search Brave + tier-фильтр).

## Задача

Live smoke web_search на сервере (после deploy'я ADR-0022) показал два дефекта качества:

1. **Subdomain не наследует tier:** `markets.businessinsider.com` уходит в `other`, хотя `businessinsider.com` в TIER2. То же будет для `markets.bloomberg.com`, `news.reuters.com`, `uk.reuters.com`. **Видимо влияет на ranking** — агент будет недооценивать tier1-источники с поддоменами.
2. **Wikipedia и mail.ru-агрегаторы попадают в результаты.** Wiki — справочник, не источник свежих новостей; для documentary вопросов есть `rag_search`. Mail.ru-агрегатор без редакторской ответственности — компиляция, не оригинал.

## Решение

### Subdomain-aware matching в `nefteboros/search/tiers.py`

Заменили `host in hosts` на helper:

```python
def _matches_any(host: str, hosts: frozenset[str]) -> bool:
    for entry in hosts:
        if host == entry or host.endswith("." + entry):
            return True
    return False
```

Применили в `classify()` и `is_blacklisted()`. Точка в `endswith` обязательна — иначе `notreuters.com` ложно матчился бы на `reuters.com` (negative test добавлен).

Покрытие: `markets.bloomberg.com → tier1`, `news.bloomberg.com → tier1`, `uk.reuters.com → tier1`, `markets.businessinsider.com → tier2`, `oil.expert.ru → tier2`, `ru.wikipedia.org → blacklist`, `finance.mail.ru → blacklist`. Subdomain-логика работает и для ENV-override (`NEFTEBOROS_WEB_*_HOSTS`).

### Минимальное расширение blacklist

Добавлены **только** два entry:
- `wikipedia.org` — справочник, не news. Любая локализация (ru/en/de/...) ловится через subdomain match.
- `mail.ru` — агрегатор без редакции. Все поддомены (`finance.mail.ru`, `news.mail.ru`, ...) ловятся через subdomain match.

Не добавляли: `tradingeconomics.com`, `investing.com` — это portals для market data, не yellow press; пусть остаются `other`. Если LLM не зацепится — ок; если плодит шум — добавим следующим точечным PR с обоснованием.

## Изменения

- [`nefteboros/search/tiers.py`](../../nefteboros/search/tiers.py): `_matches_any()` helper, `classify()` и `is_blacklisted()` стали subdomain-aware, blacklist += `wikipedia.org`, `mail.ru`. Module-docstring расширен.
- [`tests/test_search_tiers.py`](../../tests/test_search_tiers.py): новый класс `TestSubdomainMatch` (14 параметризованных кейсов на subdomain-наследование + 5 false-suffix), `test_subdomain_blacklisted`, `test_empty_host_not_blacklisted`, обновлён `test_blacklist_env_replaces_default` (subdomain через ENV).

## Тесты

`pytest tests/test_search_tiers.py tests/test_search_websearcher.py tests/test_search_brave.py`: **80 passed**.

## Deployment notes

После merge: `ssh root@server && cd /root/nefteboros && git pull && systemctl restart nefteboros`. Re-review **не нужен** — изменения только в `nefteboros/search/tiers.py` и тестах, manifest skill'а (`SKILL.md`) не тронут, `content_hash` не сдвигается. Если review-стейт всё-таки сбросится (на всякий случай) — `POST /api/skills/neftegaz_analyst/review` отработает за ~2 мин.

Smoke-проверка: повторить запросы из live smoke ADR-0022, ожидаем `markets.bloomberg.com → tier1`, отсутствие `wikipedia.org` и `mail.ru` в результатах.
