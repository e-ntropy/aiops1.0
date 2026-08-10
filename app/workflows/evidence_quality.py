"""Evidence Quality Gate：决定结束、补证据或升级 Deep，不让模型自行选模式。"""

from __future__ import annotations

from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, Field

from app.workflows.models import EvidenceStatus, WorkflowIntent, WorkflowState


class EvidenceGateDecision(StrEnum):
    COMPLETE = "complete"
    COLLECT_MORE = "collect_more"
    ESCALATE_DEEP = "escalate_deep"
    BLOCKED = "blocked"


class EvidenceQualityDimensions(BaseModel):
    observed_count: int = 0
    unavailable_or_error_count: int = 0
    source_diversity: int = 0
    type_diversity: int = 0
    scope_consistency: float = Field(default=0, ge=0, le=1)
    error_ratio: float = Field(default=0, ge=0, le=1)
    root_cause_confidence: float = Field(default=0, ge=0, le=1)


class EscalationPlan(BaseModel):
    parent_run_id: str
    child_run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex}")
    target_mode: str = "deep"
    preserve_evidence_ids: list[str] = Field(default_factory=list)
    required_evidence_types: list[str] = Field(default_factory=list)
    reason: str


class EvidenceQualityAssessment(BaseModel):
    decision: EvidenceGateDecision
    score: float = Field(ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    missing_evidence_types: list[str] = Field(default_factory=list)
    dimensions: EvidenceQualityDimensions
    escalation: EscalationPlan | None = None


def _scope_fingerprint(state: WorkflowState) -> tuple[str, str, str, str]:
    scope = state.scope
    return (
        scope.kind.value,
        scope.environment,
        scope.resource_id,
        scope.instance or scope.service,
    )


def assess_evidence_quality(
    state: WorkflowState,
    *,
    anomaly_detected: bool | None = None,
    root_cause_confidence: float | None = None,
) -> EvidenceQualityAssessment:
    """确定性 Gate；相同 State 和信号必须得到相同路由结论。"""
    live = [item for item in state.evidence if item.status == EvidenceStatus.OBSERVED]
    failed = [
        item
        for item in state.evidence
        if item.status in {EvidenceStatus.UNAVAILABLE, EvidenceStatus.ERROR}
    ]
    expected_scope = _scope_fingerprint(state)
    consistent = sum(
        1
        for item in live
        if (
            item.scope.kind.value,
            item.scope.environment,
            item.scope.resource_id,
            item.scope.instance or item.scope.service,
        )
        == expected_scope
    )
    total_relevant = len(live) + len(failed)
    scope_consistency = consistent / len(live) if live else 0.0
    error_ratio = len(failed) / total_relevant if total_relevant else 0.0
    rca_confidence = (
        root_cause_confidence
        if root_cause_confidence is not None
        else state.outcome.confidence if state.outcome.root_cause else 0.0
    )
    dimensions = EvidenceQualityDimensions(
        observed_count=len(live),
        unavailable_or_error_count=len(failed),
        source_diversity=len({item.source for item in live}),
        type_diversity=len({item.type for item in live}),
        scope_consistency=round(scope_consistency, 3),
        error_ratio=round(error_ratio, 3),
        root_cause_confidence=rca_confidence,
    )
    reasons: list[str] = []
    missing: list[str] = []
    anomaly = state.outcome.requires_escalation if anomaly_detected is None else anomaly_detected

    if live and scope_consistency < 1:
        reasons.append("现场证据与当前 TargetScope 不一致")
        return EvidenceQualityAssessment(
            decision=EvidenceGateDecision.BLOCKED,
            score=0,
            reasons=reasons,
            dimensions=dimensions,
        )
    if not live:
        reasons.append("缺少绑定当前 Scope 的现场证据")
        missing.append("observed_evidence")
        return EvidenceQualityAssessment(
            decision=EvidenceGateDecision.COLLECT_MORE,
            score=0,
            reasons=reasons,
            missing_evidence_types=missing,
            dimensions=dimensions,
        )
    if error_ratio > 0.5:
        reasons.append("超过一半数据源不可用，当前证据不完整")
        missing.append("alternative_live_source")
        return EvidenceQualityAssessment(
            decision=EvidenceGateDecision.COLLECT_MORE,
            score=round(max(0, 1 - error_ratio), 3),
            reasons=reasons,
            missing_evidence_types=missing,
            dimensions=dimensions,
        )

    diagnosis_intent = state.query.primary_intent == WorkflowIntent.FAULT_DIAGNOSIS
    needs_deep = anomaly or diagnosis_intent
    if needs_deep and dimensions.source_diversity < 2:
        reasons.append("异常或故障诊断只有单一现场来源，需要交叉取证")
        missing.append("independent_live_source")
    if diagnosis_intent and rca_confidence < 0.7:
        reasons.append("根因置信度低于 0.70")
        missing.append("root_cause_support")
    if reasons:
        return EvidenceQualityAssessment(
            decision=EvidenceGateDecision.ESCALATE_DEEP,
            score=round(
                0.4 * min(1, dimensions.source_diversity / 2)
                + 0.3 * scope_consistency
                + 0.3 * rca_confidence,
                3,
            ),
            reasons=reasons,
            missing_evidence_types=missing,
            dimensions=dimensions,
            escalation=EscalationPlan(
                parent_run_id=state.run_id,
                preserve_evidence_ids=[item.id for item in live],
                required_evidence_types=missing,
                reason="; ".join(reasons),
            ),
        )

    reasons.append("现场证据 Scope 一致且满足当前任务的最低质量要求")
    return EvidenceQualityAssessment(
        decision=EvidenceGateDecision.COMPLETE,
        score=round(
            0.4 * min(1, dimensions.source_diversity / 2)
            + 0.3 * scope_consistency
            + 0.3 * max(rca_confidence, 0.8),
            3,
        ),
        reasons=reasons,
        dimensions=dimensions,
    )
