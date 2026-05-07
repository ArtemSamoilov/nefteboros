# 2026-05-07 — Tier-first classify order + whitelist `finance.mail.ru`

**PR:** `fix/web-tier-classify-order-and-mail-whitelist`
**Связано:** [ADR-0022](../adr/0022-web-search-brave.md), [changelog 2026-05-07-web-tier-audit-pass-2](2026-05-07-web-tier-audit-pass-2.md).

## Задача

Артём поднял вопрос: «На блэклистнутых агрегаторах не будет ценных новостей вроде отчётов, прогнозах?» Эмпирическая проверка curl'ом главных страниц показала:

| Сайт | размер | нефт | газ | прогноз | спец-имена | Вердикт |
|---|---|---|---|---|---|---|
| `moneytimes.ru` | 40K | 1 | 1 | 0 | — | пусто на нашу тему — блок ОК |
| `investmint.ru` | **274 байта** | 0 | 0 | 0 | — | заглушка / JS-wall — блок ОК |
| `life.ru` | 1MB | 0 | 8 | 8 | — | газ=плита, прогноз=погода — yellow, блок ОК |
| **`finance.mail.ru`** | 421K | **13** | **10** | **15** | Brent / Газпром явно | **financial editorial — false negative блокировки через `mail.ru`** |

`finance.mail.ru` блокировался через subdomain-match `mail.ru` в blacklist, хотя сам по себе — финансовая редакция (интервью, прогнозы аналитиков РФ-брокеров, сводки). Это **false negative**, который теряет ценные source'ы для нашего use-case.

## Решение

### 1. Tier-first порядок в `classify()` — whitelist override

Поменял порядок проверок в `classify()`:

```python
# Было:
if _matches_any(h, get_blacklist()): return "blacklist"
if _matches_any(h, get_tier1()): return "tier1"
if _matches_any(h, get_tier2()): return "tier2"

# Стало:
if _matches_any(h, get_tier1()): return "tier1"
if _matches_any(h, get_tier2()): return "tier2"
if _matches_any(h, get_blacklist()): return "blacklist"
```

Семантика: **explicit tier membership имеет приоритет над blacklist subdomain match**. Это даёт способ whitelist'ить полезные subdomain'ы под blacklisted-корнями без введения отдельного whitelist set'а.

### 2. `is_blacklisted()` через `classify()`

```python
def is_blacklisted(host: str) -> bool:
    return classify(host) == "blacklist"
```

Это поддерживает консистентность: `WebSearcher` фильтрует через `is_blacklisted`, и whitelist-override автоматически работает в фильтре.

### 3. `finance.mail.ru` → TIER2

Добавлен явно с inline-комментарием. Эффекты:
- `classify("finance.mail.ru") == "tier2"` ✓
- `classify("mail.ru") == "blacklist"` ✓ (root остаётся блокированным)
- `classify("news.mail.ru") == "blacklist"` ✓ (через `mail.ru` subdomain match)
- `classify("api.finance.mail.ru") == "tier2"` ✓ (subdomain explicit TIER2 entry)

## Что НЕ добавлено в whitelist

- `news.mail.ru` — empirical probe: 4 нефт + 11 газ + 2 санкции, **0 прогноз / 0 Brent / 0 Газпром явно**. Generic news aggregator без финансовой специализации; качественно слабее `finance.mail.ru`.
- `pulse.mail.ru` — pulse-агрегатор без редакции.

Если в будущем какой-то subdomain покажет себя ценным — добавляется одной строкой в `_DEFAULT_TIER1` или `_DEFAULT_TIER2`, без изменения логики.

## Изменения

- [`nefteboros/search/tiers.py`](../../nefteboros/search/tiers.py): порядок в `classify()`, `is_blacklisted()` через `classify()`, TIER2 += `finance.mail.ru`. Module-docstring расширен — описание tier-first override.
- [`tests/test_search_tiers.py`](../../tests/test_search_tiers.py):
  - Новый класс `TestTierFirstOverride` (5 кейсов whitelist override + subdomain-наследование TIER2-entry).
  - Обновлён `TestSubdomainMatch` (убран кейс `finance.mail.ru → blacklist`).
  - Обновлён `TestIsBlacklisted` (`news.mail.ru` вместо `finance.mail.ru`).

## Тесты

`pytest tests/test_search_tiers.py tests/test_search_websearcher.py tests/test_search_brave.py`: **131 passed**.

## Deployment notes

Manifest skill'а не тронут — `content_hash` стабилен, re-review не нужен. После merge: `git pull && systemctl restart nefteboros`.
