# 2026-05-25 — Новости → состояния флагов → μ: веб-детекция, approve-gate, snapshot

**PR:** `feature/forecast-web-flags`
**Связано:** [ADR-0028](../adr/0028-web-flags-snapshot.md), [ADR-0025](../adr/0025-flags-to-mu.md) (частично superseded), [ADR-0024](../adr/0024-ou-regime-forecast.md), [ADR-0022](../adr/0022-web-search-brave.md).

## Задача

Этап 2 фичи flags→μ: новости автоматически (с подтверждением) влияют на прогноз
цены. Веб детектит состояния геополитических флагов, пересчитывает калибровку μ,
версионирует snapshot. Этап 1 (ADR-0025) сделал μ детерминированной функцией
СОСТОЯНИЙ флагов, но явно отложил в этап 2: (a) классификацию состояний из новостей,
(b) непрерывную flag→μ поверхность.

## Контекст

Принято **полу-авто с approve**: веб считает новую μ → показывает diff + источники →
применяется ТОЛЬКО после явного подтверждения. Принцип этапа 1 сохранён: LLM судит
ФАКТ (какое состояние флага, закрытый enum + цитата), число μ считает формула — не
LLM. На произвольных веб-комбинациях (вне 3 пресетов) вскрылись 3 артефакта
калибровки этапа 1 — разрешены непрерывной base-anchored поверхностью (см. ADR-0028).

## Что сделано

**Код (новый пакет `nefteboros/forecast/web_flags/`):**
- `models.py` — `FlagSource`, `DriverDetection`, `CalibrationSnapshot` (μ НЕ хранится,
  выводится через `compute_mu_from_flags`), `AssetMuDelta`, `GuardrailReport`,
  `MuProposal`. Закрытый enum состояний выводится из `scenarios.DRIVERS` (единый
  источник истины — классификатор не может разойтись с ключами цепочки).
- `detect.py` — `DriverStateClassifier` (LLM, идиома `rag/query_classifier.py`:
  hydra/kimi, temperature=0, JSON-парс) + `FlagDetector`: tier-1 поиск на драйвер →
  классификация КАЖДОГО источника в enum → **правило ≥2 различных tier-1 хостов в
  КОДЕ** (детерминированно). Конфликт → `disputed` (не меняем). Старое событие
  (event_date > 45 дн) отбрасывается.
- `snapshot.py` — `SnapshotStore`: версии `vNNNN.json` + `ACTIVE` + `changelog.jsonl`
  (полный diff-лог) + TTL (`should_refresh`).
- `guardrails.py` — Δμ-cap (гейт, не клэмп; default 35%), инвариант bear<base<bull,
  направление (профицит ⇒ ниже μ).
- `propose.py` — approve-gate: `build_proposal` (чист) → `apply_proposal(confirm=True)`
  (единственный путь, меняющий активный snapshot; без confirm → `ApprovalRequired`;
  guardrail-fail → `GuardrailBlocked` без `force`). `active_forecast` — прогноз по
  активному snapshot.

**Калибровка (`nefteboros/forecast/scenarios.py`) — разрешение артефактов 1-4:**
- `compute_mu_from_flags` переписана на **непрерывную base-anchored поверхность**
  (кусочно-линейна, per-asset; якорь на наблюдаемой μ_base, не на фиктивном $89.2).
- `partial_closure −3.27 → −2.0` (честная физика; bull-баланс −2.57 → −1.3).
- **Structural clamp** `μ ∈ [OIL_MU_FLOOR=$40, OIL_MU_CEILING=$155]` (ADR-0024
  floor/ceiling) **в самой μ-функции** — экстраполяция за пределы пресетов не
  пробивает cost-floor/demand-ceiling (артефакт 4, найден при ревью: full_reopen+
  full_lift → $24.5, full_closure → $183). Клэмп в функции, а не только в gate, т.к.
  `compute_mu_from_flags` зовётся и в обход approve-gate. Пресеты внутри → не затронуты.
- Удалены `CALM_BASELINE_BRENT`, `_DERIVED_OIL_AFFINE`. `KILIAN_USD_PER_MBPD`
  репозиционирован как литературный референс (не множитель формулы).

**CLI:** `scripts/forecast_web_flags.py` — `status` / `detect [--apply --yes --force]`
/ `forecast` / `log`.

**Docs:** ADR-0028 (детекция, approve-gate + почему не молча, guardrails, разрешение
артефактов 1-3 + supersede ADR-0025, reproducibility), этот changelog.

## Восстановление μ — непрерывная поверхность (фактический прогон)

Все 5 нефтей, все 3 пресета воспроизводятся **точно** (этап 1 давал bull ≤$0.04):

| asset | bear | base | bull | max \|diff\| |
|---|---:|---:|---:|---:|
| brent | 70.000 | 98.000 | 120.000 | 0.000 |
| wti | 66.000 | 94.000 | 115.000 | 0.000 |
| urals | 62.000 | 81.000 | 95.000 | 0.000 |
| espo | 65.000 | 92.000 | 113.000 | 0.000 |
| urals_minfin_blend | 63.000 | 83.000 | 99.000 | 0.000 |

Непрерывность в base (артефакт 3 закрыт): china weak (+0.4) → brent **$91.0**
(этап 1 давал артефактные $84.4); iran further_tightening (−0.2) → **$101.4**
(этап 1 давал инверсные $91.6 — дефицит ронял μ).

## Файлы

- **Добавлено:** `nefteboros/forecast/web_flags/{__init__,models,detect,snapshot,guardrails,propose}.py`,
  `scripts/forecast_web_flags.py`, `tests/test_web_flags.py`,
  `docs/adr/0028-web-flags-snapshot.md`, `docs/changelog/2026-05-25-web-flags-snapshot.md`.
- **Изменено:** `nefteboros/forecast/scenarios.py` (непрерывная поверхность +
  structural clamp `OIL_MU_FLOOR/CEILING`, partial_closure, удалены 2 константы),
  `tests/test_forecast_flags.py` (3 теста под новую поверхность + непрерывность +
  `TestStructuralClamp`), `.gitignore` (ignore `/data/state/web_flags/`).
- **Удалено:** — (константы убраны внутри scenarios.py).

## Тесты

Python 3.12.12 (`.venv312`, dev/prod parity), точные числа:

| Прогон | Результат |
|---|---|
| `pytest tests/test_web_flags.py` | **34 passed** |
| `pytest tests/test_forecast_flags.py` | **18 passed, 3 deselected** (network) |
| `+ test_web_flags + test_ou_sigma_anchor + test_forecast_reproducibility` | **65 passed, 9 deselected** (без регрессий) |
| `pytest tests/test_web_flags.py -m network` | **1 passed** (реальный kimi-k2p6 классификатор, 34с) |

- `test_web_flags.py` (mock-web, без сети/LLM): закрытый enum, правило ≥2 источников,
  конфликт→disputed, устаревание, парсинг LLM, версионирование snapshot, μ не хранится,
  approve-gate (confirm=False → отказ, активный не тронут), guardrails (cap/инвариант/
  блок+force), reproducibility, wiring `active_forecast` (monkeypatch, без сети).
- AST-parse всех затронутых .py — OK.

## Что НЕ в PR (отложено явно)

- **Живой Brave-fetch** — на момент #81 не прогнан (нет ключа); **прогнан в follow-up**
  (2026-05-25): реальный Brave tier-1 + kimi → `china_demand: base→weak` (2 tier-1),
  μ brent $98→$91, guardrails OK; ≥2-правило и continuous surface подтверждены вживую.
  Детали — `docs/experiments/web-flags-live-detect.md` + follow-up changelog
  `2026-05-25-web-flags-live-detect.md`. Предложение НЕ применено (advisory).
- **Авто-расписание детекции** (cron) — backlog; TTL-хук (`should_refresh`) есть.
- **θ/σ под флаги, газ/equity** — наследие non-goal ADR-0025.

## Слабые места (саморазгром)

- **Эффективная эластичность ~$17/mbpd** вместо $12 — следствие честного якоря на
  наблюдаемой base μ ($98), а не фиктивного $89.2. Для новостной реакции честнее
  ($12 supply Kilian + ~$5 репрайс риск-премии), но выше «чистого» коридора Kilian
  $10–15. Аргументация и supersede §Known-limitations этапа 1 — в ADR-0028.
- **Экстраполяция за [bull,bear] = [−1.3,+1.6] mbpd** — **закрыта structural clamp**
  ($40/$155, ADR-0024 floor/ceiling); вне пресетов внутри коридора заявляется только
  направление, на границах — жёсткий клэмп + Δμ-cap.
- **Лёгкий кинк в base** (де-эск 17.5 vs эск 16.9 mbpd) — асимметрия эластичности,
  численно $0.6/mbpd, пренебрежимо.
- **Живая Brave-выдача — прогнана в follow-up** (см. `docs/experiments/web-flags-live-detect.md`):
  классификатор корректно разложил реальные tier-1 заголовки в закрытый enum. Остаточный
  known-limitation: источник с будущей датой (2026-06-07) проходит staleness-фильтр (ловит
  только старые) → backlog (фильтр неправдоподобно-будущих дат).
