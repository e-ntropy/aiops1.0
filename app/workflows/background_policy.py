"""统一工作流进入后台队列前的执行位置与数据边界判定。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.workflows.capabilities import CapabilityId
from app.workflows.models import ScopeKind, WorkflowState


class BackgroundExecutionDecision(BaseModel):
    can_submit: bool
    capability_eligible: bool
    adapter_ready: bool = False
    required_executor: str
    redis_payload_contract: str = "task_reference_only"
    reasons: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)


def assess_background_execution(state: WorkflowState) -> BackgroundExecutionDecision:
    """拒绝会在错误主机采集数据或复制完整 WorkflowState 的后台执行。"""
    capability = CapabilityId(state.capability_id)
    reasons: list[str] = []
    prerequisites = [
        "Postgres 持久化脱敏后的工作流请求与事实状态",
        "Redis Stream 只传 task_id、incident_id 和路由元数据",
        "Worker 按 task_id 回查事实并写入结构化事件与证据",
    ]
    if state.scope.kind == ScopeKind.LOCAL_HOST and capability != CapabilityId.KNOWLEDGE_QA:
        reasons.append("本机 Scope 必须绑定发起请求的执行节点，普通 Worker 会采集到错误主机")
        prerequisites.append("部署具有 target_id 的节点采集 Agent，并校验 Worker/Target 亲和性")
        return BackgroundExecutionDecision(
            can_submit=False,
            capability_eligible=False,
            required_executor="target_affine_agent",
            reasons=reasons,
            prerequisites=prerequisites,
        )
    if capability == CapabilityId.KNOWLEDGE_QA:
        reasons.append("知识问答不依赖目标主机，语义上可后台执行")
        reasons.append("当前 diagnosis Worker 尚未接入 Capability Executor")
        return BackgroundExecutionDecision(
            can_submit=False,
            capability_eligible=True,
            required_executor="capability_worker",
            reasons=reasons,
            prerequisites=prerequisites + ["实现并验证 capability_worker 队列适配器"],
        )
    reasons.append("当前统一执行器或远程采集通道尚未提供可验证的后台适配器")
    return BackgroundExecutionDecision(
        can_submit=False,
        capability_eligible=False,
        required_executor="capability_worker",
        reasons=reasons,
        prerequisites=prerequisites,
    )
