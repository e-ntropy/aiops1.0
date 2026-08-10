"""工作流失败分类与统一降级决策。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class FailureAction(StrEnum):
    RETRY = "retry"
    USE_ALTERNATIVE = "use_alternative"
    MARK_UNAVAILABLE = "mark_unavailable"
    REQUEST_CONFIRMATION = "request_confirmation"
    DENY = "deny"
    FAIL_WORKFLOW = "fail_workflow"


class FallbackDecision(BaseModel):
    action: FailureAction
    retry_after_ms: int = Field(default=0, ge=0)
    reason: str


_RETRYABLE = {"TimeoutError", "ConnectionError", "RateLimitError", "ServiceUnavailable"}
_NON_RETRYABLE = {"PermissionError", "ValidationError", "ScopeError", "PolicyDenied"}


def decide_failure_action(
    *,
    error_type: str,
    attempt: int,
    max_retries: int,
    has_alternative: bool = False,
    required_source: bool = False,
) -> FallbackDecision:
    if error_type in _NON_RETRYABLE:
        action = FailureAction.DENY if error_type in {"PermissionError", "PolicyDenied"} else FailureAction.REQUEST_CONFIRMATION
        return FallbackDecision(action=action, reason=f"{error_type} 不允许自动重试")
    if error_type in _RETRYABLE and attempt <= max_retries:
        return FallbackDecision(
            action=FailureAction.RETRY,
            retry_after_ms=min(8000, 500 * (2 ** max(0, attempt - 1))),
            reason=f"可恢复错误，第 {attempt}/{max_retries} 次重试",
        )
    if has_alternative:
        return FallbackDecision(
            action=FailureAction.USE_ALTERNATIVE,
            reason="主数据源失败，切换已登记的只读替代数据源",
        )
    if required_source:
        return FallbackDecision(
            action=FailureAction.FAIL_WORKFLOW,
            reason="关键数据源不可用，不能生成确定性结论",
        )
    return FallbackDecision(
        action=FailureAction.MARK_UNAVAILABLE,
        reason="数据源不可用，记录缺口并继续其余只读步骤",
    )

