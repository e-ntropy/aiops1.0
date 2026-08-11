"""统一工作流不可绕过的代码层状态与作用域约束。"""

from __future__ import annotations

from app.workflows.models import (
    ConfirmationStatus,
    RiskLevel,
    WorkflowPhase,
    WorkflowState,
)

_ALLOWED_PHASE_TRANSITIONS: dict[WorkflowPhase, set[WorkflowPhase]] = {
    WorkflowPhase.UNDERSTANDING: {WorkflowPhase.CLARIFYING, WorkflowPhase.SCOPING, WorkflowPhase.FAILED},
    WorkflowPhase.CLARIFYING: {WorkflowPhase.UNDERSTANDING, WorkflowPhase.CANCELLED},
    WorkflowPhase.SCOPING: {WorkflowPhase.CLARIFYING, WorkflowPhase.READY, WorkflowPhase.FAILED},
    WorkflowPhase.READY: {WorkflowPhase.EXECUTING, WorkflowPhase.CANCELLED},
    WorkflowPhase.EXECUTING: {WorkflowPhase.WAITING_APPROVAL, WorkflowPhase.VERIFYING, WorkflowPhase.COMPLETED, WorkflowPhase.FAILED},
    WorkflowPhase.WAITING_APPROVAL: {WorkflowPhase.EXECUTING, WorkflowPhase.CANCELLED, WorkflowPhase.FAILED},
    WorkflowPhase.VERIFYING: {WorkflowPhase.COMPLETED, WorkflowPhase.EXECUTING, WorkflowPhase.FAILED},
    WorkflowPhase.COMPLETED: set(),
    WorkflowPhase.FAILED: set(),
    WorkflowPhase.CANCELLED: set(),
}


def validate_phase_transition(current: WorkflowPhase, target: WorkflowPhase) -> None:
    if target not in _ALLOWED_PHASE_TRANSITIONS[current]:
        raise ValueError(f"非法状态迁移: {current.value} -> {target.value}")


def can_execute_live_tools(state: WorkflowState) -> tuple[bool, str]:
    if not state.scope.allows_live_tools:
        return False, "TargetScope 未验证，禁止现场工具"
    if state.query.requires_confirmation and state.confirmation_status != ConfirmationStatus.CONFIRMED:
        return False, "用户确认尚未完成"
    if state.query.risk_level == RiskLevel.HIGH:
        return False, "高风险请求只能进入审批流程，不能直接执行"
    if state.phase not in {WorkflowPhase.READY, WorkflowPhase.EXECUTING}:
        return False, f"当前阶段 {state.phase.value} 不允许工具执行"
    return True, "allowed"
