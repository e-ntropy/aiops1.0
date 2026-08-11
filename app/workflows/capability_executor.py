"""AIOps Capability 的统一流式执行入口。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from app.services.rag_service import stream_chat
from app.workflows.adaptive_diagnosis import stream_adaptive_diagnosis
from app.workflows.capabilities import CapabilityId
from app.workflows.evaluation_overview import execute_evaluation_overview
from app.workflows.incident_review import execute_incident_review
from app.workflows.memory_policy import attach_memory_write_policy
from app.workflows.models import (
    EvidenceItem,
    EvidenceStatus,
    WorkflowPhase,
    WorkflowState,
    WorkflowTransition,
)
from app.workflows.policies import validate_phase_transition
from app.workflows.readonly_analysis import execute_readonly_analysis
from app.workflows.system_inspection import execute_local_inspection

RagStreamer = Callable[..., AsyncIterator[dict[str, Any]]]
AdaptiveStreamer = Callable[..., AsyncIterator[dict[str, Any]]]


def _event(
    event_type: str,
    capability_id: CapabilityId,
    message: str,
    **data: Any,
) -> dict[str, Any]:
    return {
        "type": event_type,
        "capability_id": capability_id,
        "message": message,
        "data": data,
    }


async def _stream_knowledge(
    state: WorkflowState,
    *,
    rag_streamer: RagStreamer,
) -> AsyncIterator[dict[str, Any]]:
    validate_phase_transition(state.phase, WorkflowPhase.EXECUTING)
    state.phase = WorkflowPhase.EXECUTING
    state.transitions.append(
        WorkflowTransition(
            from_phase=WorkflowPhase.READY,
            to_phase=WorkflowPhase.EXECUTING,
            reason="knowledge_rag_started",
        )
    )
    answer_parts: list[str] = []
    sources: list[str] = []
    failed = False
    async for rag_event in rag_streamer(
        state.query.raw_query,
        session_id=state.session_id,
        web_search=False,
        mcp_tools=False,
    ):
        if rag_event.get("type") == "token":
            answer_parts.append(str(rag_event.get("content") or ""))
        if rag_event.get("type") == "error":
            failed = True
        data = rag_event.get("data") or {}
        for source in data.get("sources") or []:
            if isinstance(source, str):
                sources.append(source)
        yield _event("capability_event", CapabilityId.KNOWLEDGE_QA, "RAG 知识问答", event=rag_event)
    answer = "".join(answer_parts).strip()
    if failed or not answer:
        validate_phase_transition(state.phase, WorkflowPhase.FAILED)
        state.phase = WorkflowPhase.FAILED
        state.terminal_reason = "知识问答未生成有效回答"
        state.transitions.append(
            WorkflowTransition(
                from_phase=WorkflowPhase.EXECUTING,
                to_phase=WorkflowPhase.FAILED,
                reason="rag_answer_failed_or_empty",
            )
        )
        yield _event(
            "workflow_failed",
            CapabilityId.KNOWLEDGE_QA,
            state.terminal_reason,
            state=state.model_dump(mode="json"),
        )
        return
    state.evidence.append(
        EvidenceItem(
            run_id=state.run_id,
            source="knowledge_base",
            type="rag_reference",
            status=EvidenceStatus.REFERENCE,
            summary="知识库检索与回答完成；该证据不是现场状态证明。",
            content={"sources": list(dict.fromkeys(sources))},
            confidence=0.7 if sources else 0.5,
        )
    )
    state.outcome.summary = "已基于运维知识库完成回答"
    state.outcome.report_markdown = answer
    state.outcome.next_action = "如需确认当前系统状态，可升级为状态查询"
    state.outcome.confidence = 0.7 if sources else 0.5
    validate_phase_transition(state.phase, WorkflowPhase.COMPLETED)
    state.phase = WorkflowPhase.COMPLETED
    state.transitions.append(
        WorkflowTransition(
            from_phase=WorkflowPhase.EXECUTING,
            to_phase=WorkflowPhase.COMPLETED,
            reason="knowledge_answer_generated",
        )
    )
    attach_memory_write_policy(state)
    yield _event(
        "workflow_complete",
        CapabilityId.KNOWLEDGE_QA,
        state.outcome.summary,
        state=state.model_dump(mode="json"),
    )


async def stream_capability_workflow(
    state: WorkflowState,
    *,
    rag_streamer: RagStreamer = stream_chat,
    adaptive_streamer: AdaptiveStreamer = stream_adaptive_diagnosis,
) -> AsyncIterator[dict[str, Any]]:
    capability_id = CapabilityId(state.capability_id)
    yield _event(
        "workflow_start",
        capability_id,
        f"开始执行 {capability_id.value}",
        run_id=state.run_id,
        strategy=state.execution_strategy,
    )
    if capability_id == CapabilityId.KNOWLEDGE_QA:
        async for event in _stream_knowledge(state, rag_streamer=rag_streamer):
            yield event
        return
    if capability_id in {CapabilityId.SYSTEM_STATUS, CapabilityId.SYSTEM_INSPECTION}:
        result = await execute_local_inspection(state)
        yield _event(
            "workflow_complete" if result.state.phase == WorkflowPhase.COMPLETED else "workflow_failed",
            capability_id,
            result.state.outcome.summary or result.state.terminal_reason,
            result=result.model_dump(mode="json"),
        )
        return
    if capability_id == CapabilityId.ADAPTIVE_DIAGNOSIS:
        async for adaptive_event in adaptive_streamer(state):
            yield _event(
                "capability_event",
                capability_id,
                adaptive_event.get("message", ""),
                event=adaptive_event,
            )
        return
    if capability_id in {
        CapabilityId.READONLY_OPTIMIZATION,
        CapabilityId.CAPACITY_PERFORMANCE,
    }:
        result = await execute_readonly_analysis(state)
        yield _event(
            "workflow_complete" if result.state.phase == WorkflowPhase.COMPLETED else "workflow_failed",
            capability_id,
            result.analysis.summary,
            result=result.model_dump(mode="json"),
        )
        return
    if capability_id == CapabilityId.INCIDENT_REVIEW:
        result = await execute_incident_review(state)
        yield _event(
            "workflow_complete"
            if result.state.phase == WorkflowPhase.COMPLETED
            else "workflow_failed",
            capability_id,
            result.state.outcome.summary or result.state.terminal_reason,
            result=result.model_dump(mode="json"),
        )
        return
    if capability_id == CapabilityId.EVALUATION:
        result = await execute_evaluation_overview(state)
        yield _event(
            "workflow_complete"
            if result.state.phase == WorkflowPhase.COMPLETED
            else "workflow_failed",
            capability_id,
            result.state.outcome.summary or result.state.terminal_reason,
            result=result.model_dump(mode="json"),
        )
        return
    raise RuntimeError(f"Capability Registry 与统一执行器不一致: {capability_id.value}")
