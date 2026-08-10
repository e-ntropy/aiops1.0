"""所有工作流共用的 ToolCall 格式、风险和幂等契约。"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, model_validator

from app.workflows.models import RiskLevel, TargetScope


class ToolExecutionStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"
    UNAVAILABLE = "unavailable"
    TIMED_OUT = "timed_out"


class ToolCallEnvelope(BaseModel):
    id: str = Field(default_factory=lambda: f"tool_{uuid4().hex}")
    run_id: str
    tool_name: str = Field(min_length=1, max_length=200)
    arguments: dict[str, Any] = Field(default_factory=dict)
    scope: TargetScope
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    permission_decision: str = Field(default="deny", pattern="^(allow|ask|deny)$")
    timeout_sec: float = Field(default=15.0, gt=0, le=300)
    max_retries: int = Field(default=2, ge=0, le=5)
    idempotency_key: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def _enforce_scope_permission_and_idempotency(self) -> ToolCallEnvelope:
        if not self.scope.allows_live_tools:
            raise ValueError("ToolCall 必须绑定已验证的非空 Scope")
        if self.permission_decision == "deny":
            raise ValueError("deny 决策不能进入 Tool Runner")
        if self.risk_level != RiskLevel.READ_ONLY and not self.idempotency_key:
            raise ValueError("非只读 ToolCall 必须提供 idempotency_key")
        return self


class ToolExecutionResult(BaseModel):
    tool_call_id: str
    status: ToolExecutionStatus
    output: dict[str, Any] = Field(default_factory=dict)
    error_type: str = ""
    error_message: str = ""
    attempts: int = Field(default=1, ge=1)
    elapsed_ms: int = Field(default=0, ge=0)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

