"""统一工作流的准备阶段：理解 Query、解析 Scope、决定是否澄清。"""

from __future__ import annotations

from app.workflows.models import (
    ConfirmationStatus,
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
    needs_confirmation = understanding.requires_confirmation or not scope.validated
    if not scope.validated and "目标环境或资源" not in understanding.missing_information:
        understanding.missing_information.append("目标环境或资源")
        understanding.requires_confirmation = True
        understanding.clarification_question = (
            understanding.clarification_question
            or "请确认要访问的环境、主机、服务或实例；不会默认使用本机数据代替。"
        )
    target_phase = WorkflowPhase.CLARIFYING if needs_confirmation else WorkflowPhase.READY
    confirmation = ConfirmationStatus.REQUIRED if needs_confirmation else ConfirmationStatus.NOT_REQUIRED
    return WorkflowState(
        session_id=session_id,
        phase=target_phase,
        query=understanding,
        scope=scope,
        confirmation_status=confirmation,
        plan=list(understanding.subtasks),
        transitions=[
            WorkflowTransition(
                from_phase=WorkflowPhase.UNDERSTANDING,
                to_phase=target_phase,
                reason="query_or_scope_requires_confirmation" if needs_confirmation else "query_and_scope_ready",
            )
        ],
    )

