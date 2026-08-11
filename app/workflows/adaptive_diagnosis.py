"""统一故障排查：快速取证 → 质量门 → Specialist 协作诊断。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any

from loguru import logger

from app.incidents.models import DiagnosisMode
from app.orchestration.diagnosis_runner import run_diagnosis_graph
from app.workflows.capabilities import CapabilityId
from app.workflows.diagnosis_audit import WorkflowDiagnosisAudit
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


async def _finish_audit_after_error(
    audit: WorkflowDiagnosisAudit,
    *,
    status: str,
    error: BaseException,
) -> None:
    """异常路径尽力关闭 AgentRun，同时保留原始业务异常。"""
    try:
        await asyncio.shield(
            audit.finish(
                status=status,
                error=f"{type(error).__name__}: {error}"[:2000],
            )
        )
    except Exception as audit_error:  # noqa: BLE001 - 不得覆盖原始异常
        logger.warning(
            "[workflow-audit] failed to finalize run {}: {}: {}",
            audit.agent_run_id,
            type(audit_error).__name__,
            audit_error,
        )


def _bounded_confidence(value: Any, default: float = 0.8) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _append_runner_evidence(
    state: WorkflowState,
    event: dict[str, Any],
    *,
    mode: str,
) -> None:
    event_type = event.get("type")
    raw_data = event.get("data")
    data = raw_data if isinstance(raw_data, dict) else {}
    if event_type == "evidence":
        requested_status = str(data.get("status") or EvidenceStatus.OBSERVED.value)
        try:
            parsed_status = EvidenceStatus(requested_status)
        except ValueError:
            parsed_status = EvidenceStatus.ERROR
        trusted_observation = (
            parsed_status == EvidenceStatus.OBSERVED
            and data.get("trusted_observation") is True
            and bool(data.get("tool_call_id"))
        )
        status = (
            EvidenceStatus.OBSERVED
            if trusted_observation
            else EvidenceStatus.ERROR
            if parsed_status == EvidenceStatus.OBSERVED
            else parsed_status
        )
        raw_metadata = data.get("metadata")
        metadata = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
        metadata.update(
            {
                "mode": mode,
                "structured_runner_event": True,
                "trusted_observation": trusted_observation,
            }
        )
        state.evidence.append(
            EvidenceItem(
                run_id=state.run_id,
                incident_id=state.incident_id,
                source=str(data.get("source") or "unknown_source"),
                type=str(data.get("evidence_type") or "runner_evidence"),
                status=status,
                summary=(
                    str(data.get("summary") or "结构化现场证据")[:4000]
                    if status != EvidenceStatus.ERROR or requested_status != "observed"
                    else "拒绝未携带受信 ToolCall ID 的 observed Evidence"
                ),
                content=(
                    dict(data.get("content") or {})
                    if isinstance(data.get("content"), dict)
                    else {"raw_value_rejected": True}
                ),
                scope=state.scope,
                tool_call_id=str(data.get("tool_call_id") or ""),
                confidence=_bounded_confidence(data.get("confidence") or 0.8)
                if status == EvidenceStatus.OBSERVED
                else 0,
                metadata=metadata,
            )
        )
    elif event_type == "tool_call":
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
                metadata={"mode": mode, "result_content_recorded": False},
            )
        )
    elif event_type == "step_complete":
        preview = str(
            data.get("result_preview")
            or event.get("message")
            or "初步证据收集步骤已完成"
        )
        state.evidence.append(
            EvidenceItem(
                run_id=state.run_id,
                incident_id=state.incident_id,
                source="fast_triage",
                type="agent_step_summary",
                status=EvidenceStatus.REFERENCE,
                summary=preview[:4000],
                confidence=0.4,
                metadata={"mode": mode, "not_live_proof": True},
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
        raise ValueError("远程 Scope 需要绑定 Target Agent；当前自适应诊断只开放本机作用域")
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
    audit = WorkflowDiagnosisAudit(state)
    await audit.start()
    yield _adaptive_event("adaptive_start", "开始快速证据收集", run_id=state.run_id)

    fast_report = ""
    fast_error = False
    try:
        async for event in runner(
            state.query.raw_query,
            session_id=state.session_id,
            diagnosis_mode=DiagnosisMode.FAST,
            cache_reports=False,
        ):
            _append_runner_evidence(state, event, mode="fast")
            new_evidence_ids = await audit.capture_state_evidence()
            await audit.record_event(event, new_evidence_ids=new_evidence_ids)
            if event.get("type") == "report":
                fast_report = str((event.get("data") or {}).get("report") or "")
            if event.get("type") == "error":
                fast_error = True
            yield _adaptive_event("fast_event", event.get("message", ""), event=event)
    except asyncio.CancelledError as exc:
        await _finish_audit_after_error(audit, status="cancelled", error=exc)
        raise
    except Exception as exc:
        await _finish_audit_after_error(audit, status="failed", error=exc)
        raise

    successful_sources = {
        item.source
        for item in state.evidence
        if item.status == EvidenceStatus.OBSERVED
    }
    if fast_report and len(successful_sources) >= 2:
        triage_confidence = 0.75
    elif fast_report and successful_sources:
        triage_confidence = 0.55
    elif fast_report:
        triage_confidence = 0.35
    else:
        triage_confidence = 0.0
    state.outcome.root_cause = fast_report[:1000] if fast_report else ""
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
        state.evidence.append(
            EvidenceItem(
                run_id=state.run_id,
                incident_id=state.incident_id,
                source="diagnosis_coordinator",
                type="diagnosis_report",
                status=EvidenceStatus.REFERENCE,
                summary="统一故障排查已基于快速取证形成诊断报告。",
                content={"execution_path": "triage_only"},
                confidence=triage_confidence,
            )
        )
        state.outcome.report_markdown = fast_report
        state.outcome.summary = "快速取证已满足诊断证据质量要求"
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
        await audit.finish(status="succeeded")
        yield _adaptive_event(
            "adaptive_complete",
            state.outcome.summary,
            effective_mode="fast",
            execution_path="triage_only",
            state=state.model_dump(mode="json"),
        )
        return

    child_run_id = assessment.escalation.child_run_id if assessment.escalation else ""
    yield _adaptive_event(
        "deep_escalation",
        "当前证据不足，保留已有 Evidence 并启动 Specialist 协作诊断",
        parent_run_id=state.run_id,
        child_run_id=child_run_id,
        preserved_evidence_ids=[item.id for item in state.evidence],
    )
    deep_report = ""
    deep_error = False
    deep_root_cause = ""
    deep_confidence = 0.0
    remediation_recommendations: list[str] = []
    seed_evidence = [
        {
            "source": item.source,
            "type": item.type,
            "summary": item.summary,
            "content": item.content,
            "score": item.confidence,
            "metadata": {
                **item.metadata,
                "workflow_evidence_id": item.id,
                "status": item.status.value,
                "seeded_from_triage": True,
            },
        }
        for item in state.evidence
        if item.status in {EvidenceStatus.OBSERVED, EvidenceStatus.REFERENCE}
    ]
    verified_memories = [
        item
        for item in state.memory.recalled_items
        if item.get("tier") == "verified_knowledge" and item.get("status") == "verified"
    ]
    try:
        async for event in runner(
            state.query.raw_query,
            session_id=state.session_id,
            diagnosis_mode=DiagnosisMode.DEEP,
            cache_reports=False,
            initial_evidence=seed_evidence,
            recalled_memories=verified_memories,
            incident_group_id=state.incident_group_id,
            incident_id=state.incident_id,
            persist_legacy_wiki=False,
        ):
            _append_runner_evidence(state, event, mode="deep")
            new_evidence_ids = await audit.capture_state_evidence()
            await audit.record_event(event, new_evidence_ids=new_evidence_ids)
            if event.get("type") == "report":
                deep_report = str((event.get("data") or {}).get("report") or "")
            if event.get("type") == "error":
                deep_error = True
            if event.get("type") == "rca":
                rca = (event.get("data") or {}).get("rca") or {}
                deep_root_cause = str(rca.get("root_cause") or "")
                deep_confidence = _bounded_confidence(rca.get("confidence"), 0.7)
            if event.get("type") == "remediation":
                remediation = (event.get("data") or {}).get("remediation") or {}
                remediation_recommendations = [
                    str(item)
                    for item in list(remediation.get("steps") or [])
                    if str(item).strip()
                ]
            yield _adaptive_event("deep_event", event.get("message", ""), event=event)
    except asyncio.CancelledError as exc:
        await _finish_audit_after_error(audit, status="cancelled", error=exc)
        raise
    except Exception as exc:
        await _finish_audit_after_error(audit, status="failed", error=exc)
        raise

    if deep_error or not deep_report:
        validate_phase_transition(state.phase, WorkflowPhase.FAILED)
        state.phase = WorkflowPhase.FAILED
        state.terminal_reason = "Specialist 协作诊断未生成可验证报告"
        state.transitions.append(
            WorkflowTransition(
                from_phase=WorkflowPhase.EXECUTING,
                to_phase=WorkflowPhase.FAILED,
                reason="deep_diagnosis_failed_or_empty",
            )
        )
        await audit.finish(status="failed", error=state.terminal_reason)
        yield _adaptive_event(
            "adaptive_failed",
            state.terminal_reason,
            state=state.model_dump(mode="json"),
        )
        return
    has_structured_deep_evidence = any(
        item.metadata.get("structured_runner_event")
        and item.metadata.get("mode") == "deep"
        for item in state.evidence
    )
    state.evidence.append(
        EvidenceItem(
            run_id=child_run_id or state.run_id,
            incident_id=state.incident_id,
            source="deep_diagnosis",
            type="diagnosis_report",
            status=EvidenceStatus.REFERENCE,
            summary=(
                "Specialist 协作诊断已生成报告，结构化 Evidence 已并入统一状态。"
                if has_structured_deep_evidence
                else "Specialist 协作诊断已生成报告，部分内部 Evidence 由运行审计链保存。"
            ),
            content={"parent_run_id": state.run_id, "child_run_id": child_run_id},
            confidence=0.7,
            metadata={
                "mode": "deep",
                "legacy_evidence_adapter": not has_structured_deep_evidence,
            },
        )
    )
    state.outcome.root_cause = deep_root_cause or deep_report[:1000]
    state.outcome.confidence = deep_confidence or 0.7
    state.outcome.recommendations = remediation_recommendations
    state.outcome.report_markdown = deep_report
    state.outcome.summary = "已完成 Specialist 多智能体协作诊断并形成报告"
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
    await audit.finish(status="succeeded")
    yield _adaptive_event(
        "adaptive_complete",
        state.outcome.summary,
        effective_mode="deep",
        execution_path="specialist_escalated",
        child_run_id=child_run_id,
        state=state.model_dump(mode="json"),
    )
