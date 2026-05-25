"""Веб-детекция геополитических флагов → версионируемая калибровка μ (ADR-0028, этап 2).

Полу-авто с approve: tier-1 веб → LLM классифицирует состояния драйверов в закрытый
enum → детерминированная цепочка этапа 1 (`compute_mu_from_flags`) считает новую μ →
diff + источники показываются человеку → применяется ТОЛЬКО после подтверждения.

Публичный поток:
    store = SnapshotStore()
    proposal = propose_from_web(store)        # детекция + diff (ничего не меняет)
    print(proposal.human_summary())
    apply_proposal(store, proposal, confirm=True)   # явный approve
    active_forecast("brent", "12m")           # прогноз по активному snapshot

См. ADR-0028, ADR-0025 (цепочка флаги→μ), nefteboros/search (tier-1 фильтр).
"""

from nefteboros.forecast.web_flags.detect import (
    DriverStateClassifier,
    FlagDetector,
    SourceVerdict,
)
from nefteboros.forecast.web_flags.models import (
    AssetMuDelta,
    CalibrationSnapshot,
    DriverDetection,
    FlagSource,
    GuardrailReport,
    MuProposal,
    valid_states,
)
from nefteboros.forecast.web_flags.propose import (
    ApprovalRequired,
    GuardrailBlocked,
    active_forecast,
    apply_proposal,
    build_proposal,
    propose_from_web,
)
from nefteboros.forecast.web_flags.snapshot import SnapshotStore

__all__ = [
    # models
    "CalibrationSnapshot",
    "DriverDetection",
    "FlagSource",
    "AssetMuDelta",
    "GuardrailReport",
    "MuProposal",
    "valid_states",
    # detection
    "FlagDetector",
    "DriverStateClassifier",
    "SourceVerdict",
    # store
    "SnapshotStore",
    # approve-gate
    "build_proposal",
    "propose_from_web",
    "apply_proposal",
    "active_forecast",
    "ApprovalRequired",
    "GuardrailBlocked",
]
