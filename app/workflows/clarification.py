"""二次确认 Loop：保留原 Query，追加用户补充并重新理解与解析 Scope。"""

from __future__ import annotations

from copy import deepcopy

from app.workflows.models import (
    ClarificationTurn,
    ConfirmationStatus,
    RiskLevel,
    WorkflowPhase,
    WorkflowState,
    WorkflowTransition,
)
from app.workflows.query_understanding import understand_query
from app.workflows.scope import resolve_scope


class ClarificationLimitReached(ValueError):
    """澄清轮数超过预算。"""


async def apply_clarification(
    state: WorkflowState,
    answer: str,
    *,
    use_llm: bool = True,
) -> WorkflowState:
    if state.phase != WorkflowPhase.CLARIFYING:
        raise ValueError("只有 clarifying 状态可以接受补充信息")
    if len(state.clarification_history) >= state.budgets.max_clarification_rounds:
        raise ClarificationLimitReached("已达到最大澄清轮数，必须安全终止或转人工")
    clean_answer = answer.strip()
    if not clean_answer:
        raise ValueError("澄清回答不能为空")

    updated = deepcopy(state)
    original_raw = state.query.raw_query
    question = state.query.clarification_question or "请补充执行所需信息"
    updated.clarification_history.append(
        ClarificationTurn(question=question, answer=clean_answer)
    )
    contextual_query = original_raw + "\n用户补充：" + clean_answer
    understanding = await understand_query(contextual_query, use_llm=use_llm)
    # Scope 必须基于“原请求 + 用户补充”解析；随后再恢复不可变的 raw_query。
    scope = resolve_scope(understanding)
    understanding.raw_query = original_raw

    # 高风险只能保持或升级，澄清不能把原请求偷偷降权。
    if state.query.risk_level == RiskLevel.HIGH:
        understanding.risk_level = RiskLevel.HIGH
    still_missing = not scope.validated
    if still_missing:
        understanding.requires_confirmation = True
        understanding.clarification_question = (
            understanding.clarification_question
            or "仍无法确认目标资源，请提供明确的主机、服务、容器或选择本机。"
        )
        target_phase = WorkflowPhase.CLARIFYING
        status = ConfirmationStatus.REQUIRED
    else:
        # 澄清只完成目标确认；高风险操作仍由独立 Approval 流程控制。
        understanding.requires_confirmation = False
        target_phase = WorkflowPhase.READY
        status = ConfirmationStatus.CONFIRMED

    updated.query = understanding
    updated.scope = scope
    updated.plan = list(understanding.subtasks)
    updated.phase = target_phase
    updated.confirmation_status = status
    updated.transitions.extend(
        [
            WorkflowTransition(
                from_phase=WorkflowPhase.CLARIFYING,
                to_phase=WorkflowPhase.UNDERSTANDING,
                reason="user_clarification_received",
            ),
            WorkflowTransition(
                from_phase=WorkflowPhase.UNDERSTANDING,
                to_phase=target_phase,
                reason="clarification_resolved" if not still_missing else "clarification_still_incomplete",
            ),
        ]
    )
    return updated
