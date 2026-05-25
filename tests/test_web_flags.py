"""Mock-web тесты слоя «новости → флаги → μ» (ADR-0028, этап 2).

Без сети и без LLM: WebSearcher и классификатор подменяются fake-объектами.
Покрытие: детекция + правило ≥2 tier-1 источников, конфликт/устаревание,
версионирование snapshot, approve-gate (нет молчаливого авто), guardrails
(Δμ-cap + инвариант + направление), reproducibility, формат предложения.

μ-числа НЕ хардкодятся жёстко в большинстве тестов — проверяются ОТНОШЕНИЯ
(изменилось/выросло/в пределах cap), чтобы тесты пережили рекалибровку цепочки.
"""

from __future__ import annotations

from datetime import date

import pytest

from nefteboros.forecast.scenarios import DRIVER_BASE_STATES, OIL_ASSETS
from nefteboros.forecast.web_flags import (
    ApprovalRequired,
    CalibrationSnapshot,
    FlagDetector,
    GuardrailBlocked,
    SnapshotStore,
    apply_proposal,
    build_proposal,
    propose_from_web,
    valid_states,
)
from nefteboros.forecast.web_flags import guardrails as gr
from nefteboros.forecast.web_flags.detect import (
    DRIVER_QUERIES,
    SourceVerdict,
    _parse_verdicts,
)
from nefteboros.forecast.web_flags.models import AssetMuDelta
from nefteboros.search import SearchHit


# =============================================================================
# Fakes — ни сети, ни LLM
# =============================================================================


def hit(host: str, title: str = "Oil market update", snippet: str = "", tier: str = "tier1") -> SearchHit:
    return SearchHit(
        title=title,
        url=f"https://{host}/article",
        hostname=host,
        snippet=snippet or title,
        tier=tier,
    )


class FakeSearcher:
    def __init__(self, by_query: dict[str, list[SearchHit]] | None = None, default: list[SearchHit] | None = None):
        self.by_query = by_query or {}
        self.default = default or []
        self.calls = 0

    def search(self, query, k=6, freshness="pw", tier_filter="all"):
        self.calls += 1
        return list(self.by_query.get(query, self.default))


class FakeClassifier:
    """Возвращает заранее заданные вердикты по драйверу (выровнены с hits)."""

    def __init__(self, by_driver: dict[str, list[SourceVerdict]] | None = None):
        self.by_driver = by_driver or {}

    def classify_sources(self, driver, hits):
        v = self.by_driver.get(driver)
        if v is None:
            return [SourceVerdict("none") for _ in hits]
        return list(v)


def detector_for(driver, hits, verdicts, **kw):
    """FlagDetector с fake-зависимостями для одного драйвера."""
    searcher = FakeSearcher(by_query={DRIVER_QUERIES[driver]: hits})
    classifier = FakeClassifier({driver: verdicts})
    return FlagDetector(searcher, classifier, **kw)


# =============================================================================
# Закрытый enum
# =============================================================================


class TestClosedEnum:
    def test_enum_matches_stage1_drivers(self):
        from nefteboros.forecast.scenarios import DRIVERS

        for driver, states in DRIVERS.items():
            assert set(valid_states(driver)) == set(states)

    def test_state_outside_enum_ignored(self):
        # LLM выдал состояние вне enum → не учитывается, смены нет.
        hits = [hit("reuters.com"), hit("bloomberg.com")]
        verdicts = [SourceVerdict("totally_made_up", 0.9), SourceVerdict("totally_made_up", 0.9)]
        d = detector_for("hormuz", hits, verdicts).detect_driver("hormuz", "blocked")
        assert d.changed is False
        assert d.detected_state == "blocked"


# =============================================================================
# Детекция + правило ≥2 tier-1 источников
# =============================================================================


class TestDetectionTwoSourceRule:
    def test_two_tier1_sources_trigger_change(self):
        hits = [hit("reuters.com"), hit("bloomberg.com")]
        verdicts = [SourceVerdict("partial_reopen", 0.9, "2026-05-20"), SourceVerdict("partial_reopen", 0.8, "2026-05-21")]
        d = detector_for("hormuz", hits, verdicts).detect_driver("hormuz", "blocked", as_of=date(2026, 5, 25))
        assert d.changed is True
        assert d.detected_state == "partial_reopen"
        assert d.tier1_support == 2
        assert 0.0 < d.confidence <= 1.0

    def test_single_source_insufficient(self):
        hits = [hit("reuters.com")]
        verdicts = [SourceVerdict("partial_reopen", 0.95, "2026-05-20")]
        d = detector_for("hormuz", hits, verdicts).detect_driver("hormuz", "blocked", as_of=date(2026, 5, 25))
        assert d.changed is False
        assert d.detected_state == "blocked"

    def test_two_hits_same_host_count_once(self):
        # Два результата с ОДНОГО хоста = один источник → недостаточно.
        hits = [hit("reuters.com", "a"), hit("reuters.com", "b")]
        verdicts = [SourceVerdict("partial_reopen", 0.9, "2026-05-20"), SourceVerdict("partial_reopen", 0.9, "2026-05-21")]
        d = detector_for("hormuz", hits, verdicts).detect_driver("hormuz", "blocked", as_of=date(2026, 5, 25))
        assert d.changed is False

    def test_conflict_marks_disputed_no_change(self):
        hits = [hit("reuters.com"), hit("bloomberg.com"), hit("ft.com"), hit("wsj.com")]
        verdicts = [
            SourceVerdict("partial_reopen", 0.9, "2026-05-20"),
            SourceVerdict("partial_reopen", 0.9, "2026-05-21"),
            SourceVerdict("partial_closure", 0.9, "2026-05-22"),
            SourceVerdict("partial_closure", 0.9, "2026-05-23"),
        ]
        d = detector_for("hormuz", hits, verdicts).detect_driver("hormuz", "blocked", as_of=date(2026, 5, 25))
        assert d.disputed is True
        assert d.changed is False
        assert d.detected_state == "blocked"

    def test_stale_event_dropped(self):
        # Старая новость (>45 дней) не считается изменением.
        hits = [hit("reuters.com"), hit("bloomberg.com")]
        verdicts = [SourceVerdict("partial_reopen", 0.9, "2026-01-01"), SourceVerdict("partial_reopen", 0.9, "2026-01-02")]
        d = detector_for("hormuz", hits, verdicts, max_event_age_days=45).detect_driver(
            "hormuz", "blocked", as_of=date(2026, 5, 25)
        )
        assert d.changed is False

    def test_unparseable_date_not_treated_stale(self):
        hits = [hit("reuters.com"), hit("bloomberg.com")]
        verdicts = [SourceVerdict("partial_reopen", 0.9, None), SourceVerdict("partial_reopen", 0.9, "недавно")]
        d = detector_for("hormuz", hits, verdicts).detect_driver("hormuz", "blocked", as_of=date(2026, 5, 25))
        assert d.changed is True

    def test_non_tier1_hit_ignored(self):
        hits = [hit("reuters.com"), hit("randomblog.com", tier="other")]
        verdicts = [SourceVerdict("partial_reopen", 0.9, "2026-05-20"), SourceVerdict("partial_reopen", 0.9, "2026-05-21")]
        d = detector_for("hormuz", hits, verdicts).detect_driver("hormuz", "blocked", as_of=date(2026, 5, 25))
        # только 1 tier-1 источник → недостаточно
        assert d.changed is False

    def test_unknown_driver_raises(self):
        with pytest.raises(ValueError, match="Unknown driver"):
            FlagDetector(FakeSearcher(), FakeClassifier()).detect_driver("nonsense", "x")

    def test_detect_all_covers_every_driver(self):
        det = FlagDetector(FakeSearcher(), FakeClassifier())  # пустая выдача
        out = det.detect_all(dict(DRIVER_BASE_STATES))
        assert {d.driver for d in out} == set(DRIVER_BASE_STATES)
        assert all(not d.changed for d in out)  # нет новостей → ничего не меняется


# =============================================================================
# Парсинг ответа LLM
# =============================================================================


class TestParseVerdicts:
    def test_markdown_fence_and_indices(self):
        raw = '```json\n{"verdicts":[{"i":0,"state":"partial_reopen","event_date":"2026-05-20","confidence":0.9}]}\n```'
        v = _parse_verdicts(raw, 2)
        assert len(v) == 2
        assert v[0].state == "partial_reopen" and v[0].confidence == 0.9
        assert v[1].state == "none"  # не упомянут → none

    def test_out_of_range_index_ignored(self):
        raw = '{"verdicts":[{"i":5,"state":"partial_reopen","confidence":0.9}]}'
        v = _parse_verdicts(raw, 2)
        assert all(x.state == "none" for x in v)

    def test_no_json_raises(self):
        with pytest.raises(ValueError, match="No JSON"):
            _parse_verdicts("полный мусор без json", 1)


# =============================================================================
# Версионируемый snapshot
# =============================================================================


class TestSnapshotStore:
    def test_seed_on_empty(self, tmp_path):
        store = SnapshotStore(tmp_path)
        active = store.load_active()
        assert active.version == 0
        assert active.flag_states == dict(DRIVER_BASE_STATES)
        # seed μ == замороженная base (обратная совместимость)
        from nefteboros.forecast.scenarios import ASSET_PARAMS

        assert active.mu("brent") == pytest.approx(ASSET_PARAMS["brent"]["base"].mu_0)

    def test_commit_increments_and_links_parent(self, tmp_path):
        store = SnapshotStore(tmp_path)
        store.load_active()  # seed v0
        snap = CalibrationSnapshot(flag_states={**DRIVER_BASE_STATES, "hormuz": "partial_reopen"})
        committed = store.commit(snap)
        assert committed.version == 1
        assert committed.parent_version == 0
        assert store.active_version() == 1

    def test_persistence_round_trip(self, tmp_path):
        store = SnapshotStore(tmp_path)
        store.load_active()
        store.commit(CalibrationSnapshot(flag_states={**DRIVER_BASE_STATES, "hormuz": "full_reopen"}))
        # новый store на той же папке видит активную версию
        store2 = SnapshotStore(tmp_path)
        assert store2.active_version() == 1
        assert store2.load_active().flag_states["hormuz"] == "full_reopen"

    def test_mu_not_persisted(self, tmp_path):
        store = SnapshotStore(tmp_path)
        store.load_active()
        text = (tmp_path / "v0000.json").read_text(encoding="utf-8")
        assert '"mu"' not in text and "mu_0" not in text  # μ выводится, не хранится
        assert "flag_states" in text

    def test_changelog_appended(self, tmp_path):
        store = SnapshotStore(tmp_path)
        store.load_active()  # seed
        store.commit(CalibrationSnapshot(flag_states=dict(DRIVER_BASE_STATES)), log_action="apply")
        actions = [e["action"] for e in store.read_log()]
        assert "seed" in actions and "apply" in actions

    def test_should_refresh_fresh_vs_stale(self, tmp_path):
        store = SnapshotStore(tmp_path)
        store.load_active()  # as_of = now
        assert store.should_refresh(ttl_hours=24) is False
        old = CalibrationSnapshot(flag_states=dict(DRIVER_BASE_STATES), as_of="2020-01-01T00:00:00+00:00")
        store.commit(old)
        assert store.should_refresh(ttl_hours=24) is True


# =============================================================================
# Approve-gate
# =============================================================================


def _proposal_with_change(store, cap_pct=gr.DEFAULT_CAP_PCT):
    base = store.load_active()
    hits = [hit("reuters.com"), hit("bloomberg.com")]
    verdicts = [SourceVerdict("partial_reopen", 0.9, "2026-05-20"), SourceVerdict("partial_reopen", 0.85, "2026-05-21")]
    det = detector_for("hormuz", hits, verdicts)
    detections = det.detect_all(base.flag_states)
    return build_proposal(base, detections, cap_pct=cap_pct)


class TestApproveGate:
    def test_proposal_has_changes_and_deltas(self, tmp_path):
        store = SnapshotStore(tmp_path)
        prop = _proposal_with_change(store)
        assert prop.has_changes is True
        assert len(prop.deltas) == len(OIL_ASSETS)
        brent = next(x for x in prop.deltas if x.asset == "brent")
        assert brent.new_mu < brent.old_mu  # reopen ⇒ μ падает

    def test_no_change_when_no_news(self, tmp_path):
        store = SnapshotStore(tmp_path)
        base = store.load_active()
        det = FlagDetector(FakeSearcher(), FakeClassifier())  # пусто
        prop = build_proposal(base, det.detect_all(base.flag_states))
        assert prop.has_changes is False
        assert "Изменений нет" in prop.human_summary()

    def test_confirm_false_refuses_and_keeps_active(self, tmp_path):
        store = SnapshotStore(tmp_path)
        prop = _proposal_with_change(store)
        before = store.active_version()
        with pytest.raises(ApprovalRequired):
            apply_proposal(store, prop, confirm=False)
        assert store.active_version() == before  # активный snapshot НЕ тронут
        assert "reject" in [e["action"] for e in store.read_log()]

    def test_confirm_true_applies_and_advances(self, tmp_path):
        store = SnapshotStore(tmp_path)
        prop = _proposal_with_change(store)
        committed = apply_proposal(store, prop, confirm=True)
        assert committed.version == 1
        assert store.active_version() == 1
        assert store.load_active().flag_states["hormuz"] == "partial_reopen"

    def test_human_summary_format(self, tmp_path):
        store = SnapshotStore(tmp_path)
        prop = _proposal_with_change(store)
        s = prop.human_summary()
        assert "hormuz" in s and "partial_reopen" in s
        assert "μ brent" in s
        assert "reuters.com" in s  # цитата источника


# =============================================================================
# Guardrails
# =============================================================================


class TestGuardrails:
    def test_delta_cap_pass(self):
        deltas = [AssetMuDelta(asset="brent", old_mu=98.0, new_mu=90.0)]  # ~8%
        ok, mx, viol = gr.check_delta_cap(deltas, cap_pct=0.35)
        assert ok and not viol and mx == pytest.approx(8 / 98)

    def test_delta_cap_fail(self):
        deltas = [AssetMuDelta(asset="brent", old_mu=98.0, new_mu=50.0)]  # ~49%
        ok, mx, viol = gr.check_delta_cap(deltas, cap_pct=0.35)
        assert not ok and viol

    def test_invariant_holds(self):
        ok, viol = gr.check_invariant()
        assert ok and not viol

    def test_direction_ok_for_deescalation(self, tmp_path):
        store = SnapshotStore(tmp_path)
        base = store.load_active()
        proposed = CalibrationSnapshot(flag_states={**DRIVER_BASE_STATES, "hormuz": "partial_reopen"})
        ok, viol = gr.check_direction(base, proposed)
        assert ok and not viol  # профицит ⇒ μ вниз, знак верный

    def test_blocked_proposal_requires_force(self, tmp_path):
        store = SnapshotStore(tmp_path)
        prop = _proposal_with_change(store, cap_pct=0.01)  # абсурдно жёсткий cap
        assert prop.guardrail.ok is False
        with pytest.raises(GuardrailBlocked):
            apply_proposal(store, prop, confirm=True)
        # active не тронут
        assert store.active_version() == 0
        # override проходит
        committed = apply_proposal(store, prop, confirm=True, force=True)
        assert committed.version == 1


# =============================================================================
# Reproducibility
# =============================================================================


class TestReproducibility:
    def test_same_snapshot_same_mu(self):
        s1 = CalibrationSnapshot(flag_states={**DRIVER_BASE_STATES, "hormuz": "partial_reopen"})
        s2 = CalibrationSnapshot(flag_states={**DRIVER_BASE_STATES, "hormuz": "partial_reopen"})
        assert s1.mu("brent") == s2.mu("brent")
        assert s1.mu_all() == s2.mu_all()

    def test_active_unchanged_between_proposals(self, tmp_path):
        store = SnapshotStore(tmp_path)
        mu_before = store.load_active().mu("brent")
        _proposal_with_change(store)  # просто предложение, без apply
        _proposal_with_change(store)
        assert store.load_active().mu("brent") == mu_before  # детекция не двигает активный


# =============================================================================
# active_forecast — интеграция с forecast() без сети (monkeypatch)
# =============================================================================


class TestActiveForecastWiring:
    def test_passes_active_flags_for_oil(self, tmp_path, monkeypatch):
        import nefteboros.forecast.api as api
        from nefteboros.forecast.web_flags import active_forecast

        captured = {}

        def fake_forecast(asset, horizon, *, scenario=None, flag_states=None, **kw):
            captured["asset"] = asset
            captured["flag_states"] = flag_states
            return "FORECAST_RESULT"

        monkeypatch.setattr(api, "forecast", fake_forecast)

        store = SnapshotStore(tmp_path)
        apply_proposal(store, _proposal_with_change(store), confirm=True)
        out = active_forecast("brent", "12m", store=store)
        assert out == "FORECAST_RESULT"
        assert captured["flag_states"]["hormuz"] == "partial_reopen"  # из активного snapshot

    def test_non_oil_gets_none_flags(self, tmp_path, monkeypatch):
        import nefteboros.forecast.api as api
        from nefteboros.forecast.web_flags import active_forecast

        captured = {}

        def fake_forecast(asset, horizon, *, scenario=None, flag_states=None, **kw):
            captured["flag_states"] = flag_states
            return "FC"

        monkeypatch.setattr(api, "forecast", fake_forecast)

        store = SnapshotStore(tmp_path)
        store.load_active()
        active_forecast("henry_hub", "3m", store=store)
        assert captured["flag_states"] is None  # не нефть → snapshot не применяется


# =============================================================================
# Network — реальный LLM-классификатор (требует HYDRA_API_KEY + сеть)
# =============================================================================


@pytest.mark.network
class TestRealClassifier:
    """Реальный kimi-k2p6 на правдоподобных tier-1 сниппетах (живой Brave не нужен).

    Закрывает риск «качество классификатора»: проверяет, что LLM возвращает
    парсящийся закрытый enum, извлекает дату, и distractor → none. Brave-выдача
    отдельно (нужен BRAVE_API_KEY).
    """

    def test_real_llm_hormuz_reopen(self):
        from nefteboros.forecast.web_flags.detect import DriverStateClassifier
        from nefteboros.forecast.web_flags.models import valid_states

        hits = [
            SearchHit(
                title="Tanker traffic partially resumes in Strait of Hormuz after de-escalation",
                url="https://reuters.com/a", hostname="reuters.com", tier="tier1",
                snippet="Shipping through the Strait of Hormuz partially resumed on May 20, 2026 "
                        "after a US-brokered de-escalation; several tankers returned to the route.",
            ),
            SearchHit(
                title="Oil tankers return to Hormuz as Gulf tensions ease",
                url="https://bloomberg.com/a", hostname="bloomberg.com", tier="tier1",
                snippet="A partial reopening of the strait on May 21 2026 brought crude carriers back.",
            ),
            SearchHit(
                title="Gold edges up as dollar slips",
                url="https://ft.com/a", hostname="ft.com", tier="tier1",
                snippet="Spot gold rose 0.3% as the dollar weakened; unrelated to oil logistics.",
            ),
        ]
        verdicts = DriverStateClassifier().classify_sources("hormuz", hits)
        assert len(verdicts) == 3
        allowed = set(valid_states("hormuz")) | {"none"}
        assert all(v.state in allowed for v in verdicts)  # строго закрытый enum
        assert verdicts[2].state == "none"  # gold-distractor отсеян
        # две явные новости о возобновлении → непустое hormuz-состояние реопена
        assert verdicts[0].state in {"partial_reopen", "full_reopen"}
        assert verdicts[1].state in {"partial_reopen", "full_reopen"}
