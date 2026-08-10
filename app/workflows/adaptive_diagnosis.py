"""Fast Triage → Evidence Quality Gate → Deep 的自适应诊断编排。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from app.incidents.models import DiagnosisMode
from app.orchestration.diagnosis_runner import run_diagnosis_graph
from app.workflows.capabilities import CapabilityId
from app.workflows.evidence_quality import EvidenceGateDecision, assess_evidence_quality
from app.workflows.memory_policy import attach_memory_write_policy
from app.workflows.models import (
    EvidenceItem,
    EvidenceStatus,
    ScopeKind,
    WorkflowPhase,
    WorkflowState,
    WorkflowTransition,
)
from app.workflows.policies import can_execute_live_tools, validate_phase_transition

DiagnosisRunner = Callable[..., AsyncIterator[dict[str, Any]]]


def _adaptive_event(event_type: str, message: str, **data: Any) -> dict[str, Any]:
    return {"type": event_type, "message": message, "data": data}


def _append_fast_evidence(state: WorkflowState, event: dict[str, Any]) -> None:
    event_type = event.get("type")
    data = event.get("data") or {}
    if event_type == "tool_call":
        tool_name = str(data.get("name") or "unknown_tool")
        ok = data.get("status") == "ok" and bool(data.get("read_only", False))
        state.evidence.append(
            EvidenceItem(
                run_id=state.run_id,
                incident_id=state.incident_id,
                source=tool_name,
                type="tool_execution",
                status=EvidenceStatus.OBSERVED if ok else EvidenceStatus.ERROR,
                summary=(
                    f"只读工具 {tool_name} 执行成功"
                    if ok
                    else f"工具 {tool_name} 执行失败或不是只读调用"
                ),
                content={
                    "status": data.get("status"),
                    "elapsed_ms": data.get("elapsed_ms"),
                    "result_chars": data.get("result_chars"),
                },
                scope=state.scope if ok else state.scope.model_copy(update={"validated": False}),
                confidence=0.6 if ok else 0,
                metadata={"mode": "fast", "result_content_recorded": False},
            )
        )
    elif event_type == "step_complete":
        preview = str(data.get("result_preview") or event.get("message") or "Fast step completed")
        state.evidence.append(
            EvidenceItem(
                run_id=state.run_id,
                incident_id=state.incident_id,
                source="fast_triage",
                type="agent_step_summary",
                status=EvidenceStatus.REFERENCE,
                summary=preview[:4000],
                confidence=0.4,
                metadata={"mode": "fast", "not_live_proof": True},
            )
        )


async def stream_adaptive_diagnosis(
    state: WorkflowState,
    *,
    runner: DiagnosisRunner = run_diagnosis_graph,
) -> AsyncIterator[dict[str, Any]]:
    if state.capability_id != CapabilityId.ADAPTIVE_DIAGNOSIS:
        raise ValueError("当前 State 未路由到自适应故障诊断")
    if state.scope.kind != ScopeKind.LOCAL_HOST:
        raise ValueError("旧诊断图尚未接入显式远程 Scope 绑定，当前只开放本机自适应诊断")
    allowed, reason = can_execute_live_tools(state)
    if not allowed:
        raise PermissionError(reason)
    validate_phase_transition(state.phase, WorkflowPhase.EXECUTING)
    state.phase = WorkflowPhase.EXECUTING
    state.transitions.append(
        WorkflowTransition(
            from_phase=WorkflowPhase.READY,
            to_phase=WorkflowPhase.EXECUTING,
            reason="adaptive_fast_triage_started",
        )
    )
    yield _adaptive_event("adaptive_start", "开始 Fast Triage", run_id=state.run_id)

    fast_report = ""
    fast_error = False
    async for event in runner(
        state.query.raw_query,
        session_id=state.session_id,
        diagnosis_mode=DiagnosisMode.FAST,
        cache_reports=False,
    ):
        _append_fast_evidence(state, event)
        if event.get("type") == "report":
            fast_report = str((event.get("data") or {}).get("report") or "")
        if event.get("type") == "error":
            fast_error = True
        yield _adaptive_event("fast_event", event.get("message", ""), event=event)

    successful_sources = {
        item.source
        for item in state.evidence
        if item.status == EvidenceStatus.OBSERVED and item.type == "tool_execution"
    }
    if fast_report and len(successful_sources) >= 2:
        triage_confidence = 0.75
    elif fast_report and successful_sources:
        triage_confidence = 0.55
    elif fast_report:
        triage_confidence = 0.35
    else:
        triage_confidence = 0.0
    state.outcome.root_cause = "Fast Triage 候选结论" if fast_report else ""
    state.outcome.confidence = triage_confidence
    assessment = assess_evidence_quality(
        state,
        anomaly_detected=fast_error,
        root_cause_confidence=triage_confidence,
    )
    yield _adaptive_event(
        "evidence_gate",
        f"Evidence Gate: {assessment.decision.value}",
        assessment=assessment.model_dump(mode="json"),
    )

    needs_deep = assessment.decision in {
        EvidenceGateDecision.ESCALATE_DEEP,
        EvidenceGateDecision.COLLECT_MORE,
    }
    if not needs_deep:
        state.outcome.report_markdown = fast_report
        state.outcome.summary = "Fast Triage 证据满足最低质量要求"
        state.outcome.next_action = "输出诊断报告"
        validate_phase_transition(state.phase, WorkflowPhase.COMPLETED)
        state.phase = WorkflowPhase.COMPLETED
        state.transitions.append(
            WorkflowTransition(
                from_phase=WorkflowPhase.EXECUTING,
                to_phase=WorkflowPhase.COMPLETED,
                reason="fast_evidence_quality_sufficient",
            )
        )
        attach_memory_write_policy(state)
        yield _adaptive_event(
            "adaptive_complete",
            state.outcome.summary,
            effective_mode="fast",
            state=state.model_dump(mode="json"),
        )
        return

    child_run_id = assessment.escalation.child_run_id if assessment.escalation else ""
    yield _adaptive_event(
        "deep_escalation",
        "Fast 证据不足，保留已有 Evidence 并升级 Deep",
        parent_run_id=state.run_id,
        child_run_id=child_run_id,
        preserved_evidence_ids=[item.id for item in state.evidence],
    )
    deep_report = ""
    deep_error = False
    async for event in runner(
        state.query.raw_query,
        session_id=state.session_id,
        diagnosis_mode=DiagnosisMode.DEEP,
        cache_reports=False,
    ):
        if event.get("type") == "report":
            deep_report = str((event.get("data") or {}).get("report") or "")
        if event.get("type") == "error":
            deep_error = True
        yield _adaptive_event("deep_event", event.get("message", ""), event=event)

    if deep_error or not deep_report:
        validate_phase_transition(state.phase, WorkflowPhase.FAILED)
        state.phase = WorkflowPhase.FAILED
        state.terminal_reason = "Deep 诊断未生成可验证报告"
        state.transitions.append(
            WorkflowTransition(
                from_phase=WorkflowPhase.EXECUTING,
                to_phase=WorkflowPhase.FAILED,
                reason="deep_diagnosis_failed_or_empty",
            )
        )
        yield _adaptive_event(
            "adaptive_failed",
            state.terminal_reason,
            state=state.model_dump(mode="json"),
        )
        return
    state.evidence.append(
        EvidenceItem(
            run_id=child_run_id or state.run_id,
            incident_id=state.incident_id,
            source="deep_diagnosis",
            type="diagnosis_report",
            status=EvidenceStatus.REFERENCE,
            summary="Deep 诊断已生成报告；内部 Evidence 仍由旧图审计链保存。",
            content={"parent_run_id": state.run_id, "child_run_id": child_run_id},
            confidence=0.7,
            metadata={"mode": "deep", "legacy_evidence_adapter": True},
        )
    )
    state.outcome.report_markdown = deep_report
    state.outcome.summary = "已从 Fast Triage 自适应升级 Deep 并形成报告"
    state.outcome.next_action = "人工确认根因与只读处置建议"
    state.outcome.requires_escalation = False
    validate_phase_transition(state.phase, WorkflowPhase.COMPLETED)
    state.phase = WorkflowPhase.COMPLETED
    state.transitions.append(
        WorkflowTransition(
            from_phase=WorkflowPhase.EXECUTING,
            to_phase=WorkflowPhase.COMPLETED,
            reason="adaptive_deep_report_generated",
        )
    )
    attach_memory_write_policy(state)
    yield _adaptive_event(
        "adaptive_complete",
        state.outcome.summary,
        effective_mode="deep",
        child_run_id=child_run_id,
        state=state.model_dump(mode="json"),
    )
