# 2026-05-25 — Web-flags: живой detect (evidence) + правки доков (follow-up к #81)

**PR:** follow-up к #81 (docs-only)
**Связано:** [ADR-0028](../adr/0028-web-flags-snapshot.md), [changelog #81](2026-05-25-web-flags-snapshot.md), [experiments](../experiments/web-flags-live-detect.md).

## Задача

Docs-only follow-up к #81 (уже в `main`, aa80bdc): зафиксировать живой прогон детекции
(закрыть гэп «классификатор на РЕАЛЬНОЙ выдаче»), поправить устаревшую пометку «живой
Brave не прогнан» в ADR-0028/changelog, добавить known-limitation. **Код и snapshot НЕ
трогаются.**

## Что сделано

- **`docs/experiments/web-flags-live-detect.md`** — evidence живого прогона (реальный
  Brave tier-1 + kimi-k2p6, commit 8aa4e0d): `china_demand: base→weak` (2 tier-1: Reuters
  + OilPrice) ⇒ μ brent $98→$91, guardrails OK; hormuz/opec по 1 источнику → НЕ менялись
  (≥2-правило). Tier-1 фильтр, классификатор на живой выдаче, ≥2-правило и continuous
  surface — подтверждены вживую. Включает полный дамп артефакта + воспроизведение.
- **ADR-0028 + changelog #81:** пометка «живой Brave не прогнан» → «прогнан»; DoD-чекбокс [x].
- **Known-limitation** (найдено прогоном): источник с будущей датой (2026-06-07) проходит
  staleness-фильтр (ловит только старые) → backlog (фильтр неправдоподобно-будущих дат).
- **`metrics/runs/2026-05-25_web-flags-live-detect_8aa4e0d.json`** — сырой артефакт
  (локально, gitignored по политике репо «в репо только сводный dashboard»).

## Что НЕ сделано (важно)

- **Snapshot НЕ изменён.** Предложение `china_demand→weak ⇒ μ brent $98→$91` — **advisory**,
  `applied: false`. Применение = **отдельное approve-решение координатора ПОСЛЕ demo** (с
  пересчётом forecast-документа #77 под новую базу). До demo база держится $98; цикл demo
  показывается как advisory (detect → предложение → approve-gate). By design ничего без
  явного подтверждения не применяется.

## Файлы

- **Добавлено:** `docs/experiments/web-flags-live-detect.md`,
  `docs/changelog/2026-05-25-web-flags-live-detect.md`,
  `metrics/runs/2026-05-25_web-flags-live-detect_8aa4e0d.json` (local, gitignored).
- **Изменено:** `docs/adr/0028-web-flags-snapshot.md`,
  `docs/changelog/2026-05-25-web-flags-snapshot.md`.

## Тесты

Docs-only — код не менялся; тесты #81 актуальны (34 mock-web + 18 flags + 65 combined +
1 network). Живой прогон зафиксирован в experiments (выше). AST — n/a (нет .py).
