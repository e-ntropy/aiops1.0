"""统一工作流的准备阶段：理解 Query、解析 Scope、决定是否澄清。"""

from __future__ import annotations

from app.workflows.capabilities import plan_capability
from app.workflows.memory_policy import decide_memory_reads
from app.workflows.models import (
    ConfirmationStatus,
    MemoryContext,
    WorkflowIntent,
    WorkflowPhase,
    WorkflowState,
    WorkflowTransition,
)
from app.workflows.query_understanding import understand_query
from app.workflows.scope import resolve_scope


async def prepare_workflow(
    raw_query: str,
    *,
    session_id: str = "default",
    use_llm: bool = True,
) -> WorkflowState:
    understanding = await understand_query(raw_query, use_llm=use_llm)
    scope = resolve_scope(understanding)
    capability = (
        None
        if understanding.primary_intent == WorkflowIntent.OUT_OF_SCOPE
        else plan_capability(understanding)
    )
    memory_reads = decide_memory_reads(
        intent=understanding.primary_intent,
        session_id=session_id,
        incident_id="",
        service=scope.service,
    )
    out_of_scope = understanding.primary_intent == WorkflowIntent.OUT_OF_SCOPE
    needs_confirmation = not out_of_scope and (
        understanding.requires_confirmation or not scope.validated
    )
    if not scope.validated and "目标环境或资源" not in understanding.missing_information:
        understanding.missing_information.append("目标环境或资源")
        understanding.requires_confirmation = True
        understanding.clarification_question = (
            understanding.clarification_question
            or "请确认要访问的环境、主机、服务或实例；不会默认使用本机数据代替。"
        )
    target_phase = (
        WorkflowPhase.FAILED
        if out_of_scope
        else WorkflowPhase.CLARIFYING if needs_confirmation else WorkflowPhase.READY
    )
    confirmation = ConfirmationStatus.REQUIRED if needs_confirmation else ConfirmationStatus.NOT_REQUIRED
    return WorkflowState(
        session_id=session_id,
        capability_id=capability.capability.id if capability else "",
        execution_strategy=(
            capability.capability.execution_strategy
            if capability
            else "reject_out_of_scope"
        ),
        selected_skills=capability.selected_skills if capability else [],
        allowed_tools=capability.allowed_tools if capability else [],
        memory=MemoryContext(
            read_decisions=[item.model_dump(mode="json") for item in memory_reads]
        ),
        phase=target_phase,
        query=understanding,
        scope=scope,
        confirmation_status=confirmation,
        plan=list(understanding.subtasks),
        transitions=[
            WorkflowTransition(
                from_phase=WorkflowPhase.UNDERSTANDING,
                to_phase=target_phase,
                reason=(
                    "out_of_scope_rejected"
                    if out_of_scope
                    else "query_or_scope_requires_confirmation"
                    if needs_confirmation
                    else "query_and_scope_ready"
                ),
            )
        ],
        terminal_reason=(
            "请求不属于当前 AIOps 知识、状态、巡检、诊断、优化、容量、复盘或评测范围"
            if out_of_scope
            else ""
        ),
    )
