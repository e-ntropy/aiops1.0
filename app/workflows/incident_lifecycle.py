"""诊断确认、只读计划、恢复验证、关闭与评测回流。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from app.workflows.fallbacks import FailureAction, decide_failure_action
from app.workflows.memory_policy import attach_memory_write_policy
from app.workflows.models import (
    DiagnosisConfirmation,
    EvidenceItem,
    FailureRecord,
    HumanDecisionStatus,
    LifecycleStage,
    RemediationAction,
    RemediationPlanState,
    RiskLevel,
    ScopeKind,
    WorkflowIntent,
    WorkflowPhase,
    WorkflowState,
)
from app.workflows.system_inspection import (
    FindingSeverity,
    PsutilSystemCollector,
    SystemCollector,
    SystemHealthSnapshot,
    evaluate_snapshot,
)
from app.workflows.tool_contracts import ToolCallEnvelope


class LifecycleEvalSample(BaseModel):
    schema_version: str = "1.0"
    id: str = Field(default_factory=lambda: f"eval_{uuid4().hex}")
    source_run_id: str
    incident_id: str = ""
    incident_family_id: str = ""
    review_status: str = Field(default="candidate", pattern="^candidate$")
    dataset_split: str = Field(default="quarantine", pattern="^quarantine$")
    query: str
    expected_root_cause: str
    evidence_types: list[str] = Field(default_factory=list)
    verification_status: str
    redacted: bool = True
    redaction_method: str = "human_attested"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


class IncidentClosureResult(BaseModel):
    state: WorkflowState
    eval_sample: LifecycleEvalSample


def initialize_incident_lifecycle(state: WorkflowState) -> WorkflowState:
    if state.query.primary_intent != WorkflowIntent.FAULT_DIAGNOSIS:
        raise ValueError("只有故障诊断工作流可以进入事故生命周期")
    if state.phase != WorkflowPhase.COMPLETED or not state.outcome.report_markdown:
        raise ValueError("诊断尚未完成或缺少报告")
    proposed = state.outcome.root_cause or "报告已生成，但结构化根因待人工提取确认"
    evidence_ids = [item.id for item in state.evidence]
    actions = [
        RemediationAction(
            title="采集新的系统健康快照",
            action_kind="verify",
            description="在人工确认诊断与计划后，只读采集恢复后的 CPU、内存、磁盘和进程快照。",
            execution_allowed=True,
            requires_human_confirmation=True,
            evidence_ids=evidence_ids,
        ),
        RemediationAction(
            title="人工评审优化或容量建议",
            action_kind="recommendation",
            description="只展示建议、影响和风险，不执行重启、停止、删除、扩容或配置修改。",
            risk_level=RiskLevel.MEDIUM,
            execution_allowed=False,
            requires_human_confirmation=True,
            evidence_ids=evidence_ids,
        ),
    ]
    for recommendation in state.outcome.recommendations:
        actions.append(
            RemediationAction(
                title="诊断建议",
                action_kind="recommendation",
                description=recommendation,
                risk_level=RiskLevel.MEDIUM,
                execution_allowed=False,
                requires_human_confirmation=True,
                evidence_ids=evidence_ids,
            )
        )
    state.lifecycle.stage = LifecycleStage.AWAITING_DIAGNOSIS_CONFIRMATION
    state.lifecycle.diagnosis = DiagnosisConfirmation(proposed_root_cause=proposed)
    state.lifecycle.remediation = RemediationPlanState(actions=actions)
    return state


def confirm_diagnosis(
    state: WorkflowState,
    *,
    decision: HumanDecisionStatus,
    corrected_root_cause: str = "",
    note: str = "",
) -> WorkflowState:
    if state.lifecycle.stage != LifecycleStage.AWAITING_DIAGNOSIS_CONFIRMATION:
        raise ValueError("当前生命周期阶段不允许确认诊断")
    if decision not in {
        HumanDecisionStatus.CONFIRMED,
        HumanDecisionStatus.CORRECTED,
        HumanDecisionStatus.REJECTED,
    }:
        raise ValueError("诊断决策必须是 confirmed/corrected/rejected")
    if decision == HumanDecisionStatus.CORRECTED and not corrected_root_cause.strip():
        raise ValueError("纠正诊断时必须提供 corrected_root_cause")
    diagnosis = state.lifecycle.diagnosis
    diagnosis.status = decision
    diagnosis.note = note
    diagnosis.decided_at = datetime.now(timezone.utc)
    diagnosis.confirmed_root_cause = (
        corrected_root_cause.strip()
        if decision == HumanDecisionStatus.CORRECTED
        else diagnosis.proposed_root_cause if decision == HumanDecisionStatus.CONFIRMED else ""
    )
    if decision == HumanDecisionStatus.REJECTED:
        state.lifecycle.stage = LifecycleStage.NOT_RECOVERED
        state.outcome.next_action = "补充证据并重新诊断"
    else:
        state.outcome.root_cause = diagnosis.confirmed_root_cause
        state.lifecycle.stage = LifecycleStage.AWAITING_PLAN_CONFIRMATION
    return state


def confirm_remediation_plan(
    state: WorkflowState,
    *,
    decision: HumanDecisionStatus,
    note: str = "",
) -> WorkflowState:
    if state.lifecycle.stage != LifecycleStage.AWAITING_PLAN_CONFIRMATION:
        raise ValueError("当前生命周期阶段不允许确认计划")
    if decision not in {HumanDecisionStatus.CONFIRMED, HumanDecisionStatus.REJECTED}:
        raise ValueError("计划决策必须是 confirmed/rejected")
    state.lifecycle.remediation.status = decision
    state.lifecycle.remediation.note = note
    state.lifecycle.remediation.decided_at = datetime.now(timezone.utc)
    state.lifecycle.stage = (
        LifecycleStage.AWAITING_VERIFICATION
        if decision == HumanDecisionStatus.CONFIRMED
        else LifecycleStage.NOT_RECOVERED
    )
    state.outcome.next_action = (
        "采集新的只读快照验证恢复"
        if decision == HumanDecisionStatus.CONFIRMED
        else "修改计划后重新确认"
    )
    return state


def _latest_baseline(state: WorkflowState) -> EvidenceItem | None:
    return next(
        (
            item
            for item in reversed(state.evidence)
            if item.type == "system_health_snapshot"
            and item.content
            and item.scope == state.scope
        ),
        None,
    )


def _snapshot_pressure(snapshot: SystemHealthSnapshot) -> dict[str, float]:
    return {
        "cpu": snapshot.cpu_percent,
        "memory": snapshot.memory_percent,
        "swap": snapshot.swap_percent,
        "disk": max((item.percent for item in snapshot.disks), default=0),
    }


async def verify_recovery(
    state: WorkflowState,
    *,
    collector: SystemCollector | None = None,
    retry_sleep: Any | None = None,
) -> WorkflowState:
    if state.lifecycle.stage not in {
        LifecycleStage.AWAITING_VERIFICATION,
        LifecycleStage.NOT_RECOVERED,
        LifecycleStage.INCONCLUSIVE,
    }:
        raise ValueError("当前生命周期阶段不允许恢复验证")
    if state.lifecycle.remediation.status != HumanDecisionStatus.CONFIRMED:
        raise PermissionError("处置/验证计划尚未人工确认")
    if state.scope.kind != ScopeKind.LOCAL_HOST or not state.scope.validated:
        raise ValueError("当前恢复验证只支持已验证的本机 Scope")
    envelope = ToolCallEnvelope(
        run_id=state.run_id,
        tool_name="get_system_health_snapshot",
        arguments={"process_limit": 10, "purpose": "recovery_verification"},
        scope=state.scope,
        permission_decision="allow",
        max_retries=state.budgets.max_tool_retries,
    )
    active_collector = collector or PsutilSystemCollector()
    started = perf_counter()
    attempts = 0
    snapshot: SystemHealthSnapshot | None = None
    while snapshot is None:
        attempts += 1
        try:
            snapshot = await asyncio.to_thread(active_collector.collect, 10)
        except Exception as exc:  # noqa: BLE001 - 转为统一失败与降级契约
            decision = decide_failure_action(
                error_type=type(exc).__name__,
                attempt=attempts,
                max_retries=envelope.max_retries,
                required_source=True,
            )
            state.failures.append(
                FailureRecord(
                    stage="recovery_verification",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    attempt=attempts,
                    retryable=decision.action == FailureAction.RETRY,
                    fallback_action=decision.action,
                    tool_name=envelope.tool_name,
                )
            )
            if decision.action == FailureAction.RETRY:
                delay = decision.retry_after_ms / 1000
                if retry_sleep is None:
                    await asyncio.sleep(delay)
                else:
                    maybe_awaitable = retry_sleep(delay)
                    if hasattr(maybe_awaitable, "__await__"):
                        await maybe_awaitable
                continue
            state.lifecycle.stage = LifecycleStage.INCONCLUSIVE
            state.lifecycle.verification.status = LifecycleStage.INCONCLUSIVE
            state.lifecycle.verification.summary = (
                "恢复验证数据源不可用，不能仅凭模型文本判定恢复。"
            )
            state.lifecycle.verification.checked_at = datetime.now(timezone.utc)
            state.outcome.next_action = "检查采集通道后重试恢复验证"
            return state
    findings = evaluate_snapshot(snapshot)
    baseline = _latest_baseline(state)
    evidence = EvidenceItem(
        run_id=state.run_id,
        incident_id=state.incident_id,
        source="local_system",
        type="recovery_verification_snapshot",
        summary=f"恢复验证快照采集成功，当前发现 {len(findings)} 个阈值异常",
        content=snapshot.model_dump(mode="json"),
        scope=state.scope,
        tool_call_id=envelope.id,
        metadata={
            "lifecycle_stage": "verification",
            "attempts": attempts,
            "elapsed_ms": int((perf_counter() - started) * 1000),
        },
    )
    state.evidence.append(evidence)
    current_pressure = _snapshot_pressure(snapshot)
    if any(item.severity == FindingSeverity.CRITICAL for item in findings):
        status = LifecycleStage.NOT_RECOVERED
        summary = "恢复验证失败：当前仍存在 critical 资源压力。"
    elif baseline is None:
        status = LifecycleStage.INCONCLUSIVE
        summary = "已取得当前快照，但缺少同 Scope 的结构化基线，无法证明恢复。"
    else:
        baseline_snapshot = SystemHealthSnapshot.model_validate(baseline.content)
        before = _snapshot_pressure(baseline_snapshot)
        improved = all(
            current_pressure[name] < before[name]
            for name in current_pressure
            if before[name] >= 80
        )
        if improved and not findings:
            status = LifecycleStage.RECOVERED
            summary = "恢复已验证：原高压指标下降，当前快照未越过固定阈值。"
        else:
            status = LifecycleStage.NOT_RECOVERED
            summary = "恢复未验证：指标未充分下降或仍存在阈值异常。"
    state.lifecycle.stage = status
    state.lifecycle.verification.status = status
    state.lifecycle.verification.baseline_evidence_id = baseline.id if baseline else ""
    state.lifecycle.verification.verification_evidence_id = evidence.id
    state.lifecycle.verification.summary = summary
    state.lifecycle.verification.checked_at = datetime.now(timezone.utc)
    state.outcome.next_action = "关闭事故" if status == LifecycleStage.RECOVERED else "继续观察或重新诊断"
    return state


def close_incident(
    state: WorkflowState,
    *,
    closed_by: str,
    redacted_query: str,
    redaction_passed: bool,
) -> IncidentClosureResult:
    if state.lifecycle.stage != LifecycleStage.RECOVERED:
        raise ValueError("只有恢复验证成功后才能关闭事故")
    if state.lifecycle.diagnosis.status not in {
        HumanDecisionStatus.CONFIRMED,
        HumanDecisionStatus.CORRECTED,
    }:
        raise ValueError("根因尚未人工确认")
    if state.lifecycle.remediation.status != HumanDecisionStatus.CONFIRMED:
        raise ValueError("计划尚未人工确认")
    if not redaction_passed or not redacted_query.strip():
        raise ValueError("脱敏未通过，禁止关闭并生成评测样本")
    eval_sample = LifecycleEvalSample(
        source_run_id=state.run_id,
        incident_id=state.incident_id,
        query=redacted_query.strip(),
        expected_root_cause=state.lifecycle.diagnosis.confirmed_root_cause,
        evidence_types=list(dict.fromkeys(item.type for item in state.evidence)),
        verification_status=state.lifecycle.verification.status,
        metadata={"scope_kind": state.scope.kind, "source": "closed_incident"},
    )
    state.lifecycle.stage = LifecycleStage.CLOSED
    state.lifecycle.closure.closed = True
    state.lifecycle.closure.closed_at = datetime.now(timezone.utc)
    state.lifecycle.closure.closed_by = closed_by
    state.lifecycle.closure.redaction_passed = True
    state.lifecycle.closure.eval_sample_id = eval_sample.id
    attach_memory_write_policy(
        state,
        human_root_cause_confirmed=True,
        remediation_verified=True,
        incident_closed=True,
        redaction_passed=True,
    )
    state.outcome.next_action = "事故已关闭，进入评测回归和知识复审"
    return IncidentClosureResult(state=state, eval_sample=eval_sample)
