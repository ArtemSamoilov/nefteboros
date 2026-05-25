# Web-flags: живой прогон детекции (этап 2, ADR-0028)

**Прогон:** 2026-05-25T06:58Z · **commit:** `8aa4e0d` (main, после мёрджа #81) ·
**окружение:** реальный Brave Search API (tier-1 фильтр) + kimi-k2p6 (HydraGPT), Python 3.12.

## Зачем

Закрыть риск «**качество классификатора на РЕАЛЬНОЙ выдаче**»: mock-тесты #81 (34 unit)
проверяли логику пайплайна, network-тест — классификатор на hand-fed сниппетах, но не
живую Brave-выдачу. Плюс — evidence для защиты: «новости → прогноз» работает end-to-end.

Сырой артефакт: `metrics/runs/2026-05-25_web-flags-live-detect_8aa4e0d.json` (локально,
gitignored по политике репо «в репо только сводный dashboard»; полный дамп — в конце).

## Результат по драйверам

| драйвер | prior → detected | changed | tier-1 источн. | примечание |
|---|---|:--:|:--:|---|
| hormuz | blocked → blocked | — | 0 | нет подтверждения за смену |
| iran | maximum_pressure_active → = | — | 0 | — |
| opec_plus | gradual → gradual | — | 1 | 1 источник < порога ≥2 |
| russia_cap | active → = | — | 0 | — |
| **china_demand** | **base → weak** | **✓** | **2** | Reuters + OilPrice, conf 0.88 |

Правило **≥2 tier-1 источников** отработало в обе стороны: opec (1 источник) и
hormuz/iran/russia (0) — НЕ изменились; china (2 независимых tier-1) — изменился.

## Сработавшее предложение (advisory, НЕ применено)

`china_demand: base → weak`, источники:
- **Reuters** «China oil import cut, higher US exports wrongfoot market bulls» (event_date 2026-05-08, conf 0.90)
- **OilPrice** «Chinese Refiners Slash Crude Runs to Lowest Level Since 2022» (conf 0.85)

⇒ μ (непрерывная base-anchored surface, ADR-0028): brent **$98 → $91** (−7, 7.1%);
wti $94→$87, espo $92→$85.25, urals $81→$76.25, urals_minfin_blend $83→$78.
Guardrails **OK**: max |Δμ| 7.45% ≤ cap 35%; инвариант bear<base<bull сохранён;
направление верное (профицит ⇒ ниже μ).

## Что подтверждено вживую

1. **Tier-1 фильтр** — все hits tier-1 (reuters/oilprice), мусора/жёлтой прессы нет.
2. **Классификатор на реальных заголовках** — «import cut» + «refiners slash crude runs»
   → `weak` (семантически верно), строго в закрытом enum (= ключи `DRIVERS`).
3. **Правило ≥2 источников** — консервативно в обе стороны: 1 источник μ не двигает.
4. **Continuous surface** — china weak в одиночку → **$91**, а НЕ артефактные **$84.4**
   разрывной цепочки этапа 1. Ровно ради этого переписывалась калибровка (ADR-0028).
5. **Approve-gate** — предложение удержано как advisory, НЕ применено автоматически.

## Approve-gate предотвратил рассинхрон demo (аргумент защиты)

forecast-документ (#77, в `main`) построен на **base $98**. Молчаливое применение
`china→weak` сдвинуло бы live-прогноз агента к **$91** → рассогласование «документ ($98)
vs агент ($91)» прямо на защите. Human-in-the-loop approve-gate предотвратил именно этот
failure mode: предложение **показано, но не применено**. Это конкретная демонстрация
ценности полу-авто (а не молчаливого авто): гейт сработал ровно там, где автоприменение
сломало бы согласованность.

## Статус применения

`applied: false`. Решение по apply — координатор, **ПОСЛЕ demo** (отдельное явное approve,
с пересчётом forecast-документа под новую базу). До demo snapshot держим как есть; цикл на
demo = advisory: `detect → предложение (diff + цитаты) → approve-gate` — это и есть
«новости→прогноз» + human-control.

## Known-limitation → backlog (найдено этим прогоном)

opec-источник пришёл с датой **2026-06-07 (будущее)**. Staleness-фильтр
(`max_event_age_days=45`) ловит только СТАРЫЕ события (`as_of − event_date > 45 дн`), не
неправдоподобно-БУДУЩИЕ (галлюцинация даты LLM). На результат не повлияло (1 источник <
порога ≥2). **Backlog:** отсекать `event_date > as_of + ε` (симметричный guard по будущему).

## Сырой артефакт (полный дамп прогона)

```json
{
  "run_ts": "2026-05-25T06:58:01.341716+00:00",
  "commit": "8aa4e0d",
  "kind": "web_flags_live_detect",
  "note": "LIVE прогон (реальный Brave tier-1 + kimi-k2p6). Proposal НЕ применён — применение = отдельное approve-решение координатора.",
  "seed_flag_states": {
    "hormuz": "blocked", "iran": "maximum_pressure_active", "opec_plus": "gradual",
    "russia_cap": "active", "china_demand": "base"
  },
  "detections": [
    {"driver": "hormuz", "prior": "blocked", "detected": "blocked", "changed": false, "disputed": false, "confidence": 0.0, "tier1_support": 0, "sources": []},
    {"driver": "iran", "prior": "maximum_pressure_active", "detected": "maximum_pressure_active", "changed": false, "disputed": false, "confidence": 0.0, "tier1_support": 0, "sources": []},
    {"driver": "opec_plus", "prior": "gradual", "detected": "gradual", "changed": false, "disputed": false, "confidence": 0.0, "tier1_support": 1,
     "sources": [{"hostname": "reuters.com", "state": "gradual", "event_date": "2026-06-07", "tier": "tier1", "confidence": 0.85, "title": "Reuters OPEC News | Today's Latest Stories | Reuters"}]},
    {"driver": "russia_cap", "prior": "active", "detected": "active", "changed": false, "disputed": false, "confidence": 0.0, "tier1_support": 0, "sources": []},
    {"driver": "china_demand", "prior": "base", "detected": "weak", "changed": true, "disputed": false, "confidence": 0.875, "tier1_support": 2,
     "sources": [
       {"hostname": "reuters.com", "state": "weak", "event_date": "2026-05-08", "tier": "tier1", "confidence": 0.9, "title": "China oil import cut, higher US exports wrongfoot market bulls | Reuters"},
       {"hostname": "oilprice.com", "state": "weak", "event_date": null, "tier": "tier1", "confidence": 0.85, "title": "Chinese Refiners Slash Crude Runs to Lowest Level Since 2022 | OilPrice.com"}
     ]}
  ],
  "proposal": {
    "has_changes": true,
    "changed": [{"driver": "china_demand", "from": "base", "to": "weak"}],
    "deltas": [
      {"asset": "brent", "old_mu": 98.0, "new_mu": 91.0, "delta": -7.0, "pct": 0.0714},
      {"asset": "espo", "old_mu": 92.0, "new_mu": 85.25, "delta": -6.75, "pct": 0.0734},
      {"asset": "urals", "old_mu": 81.0, "new_mu": 76.25, "delta": -4.75, "pct": 0.0586},
      {"asset": "urals_minfin_blend", "old_mu": 83.0, "new_mu": 78.0, "delta": -5.0, "pct": 0.0602},
      {"asset": "wti", "old_mu": 94.0, "new_mu": 87.0, "delta": -7.0, "pct": 0.0745}
    ],
    "guardrail": {"ok": true, "cap_pct": 0.35, "max_observed_pct": 0.0745, "invariant_ok": true, "violations": []},
    "applied": false
  }
}
```

## Воспроизведение

```bash
# нужен BRAVE_API_KEY + HYDRA_API_KEY в окружении
python scripts/forecast_web_flags.py detect          # покажет предложение (НЕ применит)
python scripts/forecast_web_flags.py detect --apply  # approve-gate: интерактивное подтверждение
```

Связано: ADR-0028, `tests/test_web_flags.py` (mock + network), `nefteboros/forecast/web_flags/`.
