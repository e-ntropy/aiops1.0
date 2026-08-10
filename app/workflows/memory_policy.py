"""Memory 读取、候选写入、可信晋升和保留策略。

本模块只做策略，不直接读写 Redis/Postgres/Milvus。存储实现必须消费这些决策，避免
Agent 自行决定把未经确认的结论写成长期知识。
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum

from pydantic import BaseModel, Field

from app.workflows.models import WorkflowIntent


class MemoryTier(StrEnum):
    SESSION = "session"
    INCIDENT = "incident"
    CANDIDATE = "candidate"
    VERIFIED_KNOWLEDGE = "verified_knowledge"


class MemoryAction(StrEnum):
    READ = "read"
    WRITE = "write"
    PROMOTE = "promote"
    ARCHIVE = "archive"
    DELETE = "delete"
    SKIP = "skip"


class MemoryDecision(BaseModel):
    tier: MemoryTier
    action: MemoryAction
    reason: str
    ttl_seconds: int | None = Field(default=None, ge=1)
    filters: dict[str, str] = Field(default_factory=dict)


class MemoryWriteContext(BaseModel):
    intent: WorkflowIntent
    incident_id: str = ""
    workflow_completed: bool = False
    human_root_cause_confirmed: bool = False
    remediation_verified: bool = False
    incident_closed: bool = False
    redaction_passed: bool = False


def decide_memory_reads(
    *,
    intent: WorkflowIntent,
    session_id: str,
    incident_id: str = "",
    service: str = "",
) -> list[MemoryDecision]:
    decisions = [
        MemoryDecision(
            tier=MemoryTier.SESSION,
            action=MemoryAction.READ,
            reason="保持当前会话连续性",
            filters={"session_id": session_id},
        )
    ]
    if intent in {WorkflowIntent.FAULT_DIAGNOSIS, WorkflowIntent.INCIDENT_REVIEW} and incident_id:
        decisions.append(
            MemoryDecision(
                tier=MemoryTier.INCIDENT,
                action=MemoryAction.READ,
                reason="同一事故的事实和人工确认优先于相似案例",
                filters={"incident_id": incident_id},
            )
        )
    if intent in {
        WorkflowIntent.KNOWLEDGE_QA,
        WorkflowIntent.FAULT_DIAGNOSIS,
        WorkflowIntent.OPTIMIZATION,
        WorkflowIntent.INCIDENT_REVIEW,
    }:
        filters = {"status": "verified"}
        if service:
            filters["service"] = service
        decisions.append(
            MemoryDecision(
                tier=MemoryTier.VERIFIED_KNOWLEDGE,
                action=MemoryAction.READ,
                reason="只召回已验证的长期知识",
                filters=filters,
            )
        )
    return decisions


def decide_memory_writes(context: MemoryWriteContext) -> list[MemoryDecision]:
    decisions = [
        MemoryDecision(
            tier=MemoryTier.SESSION,
            action=MemoryAction.WRITE,
            reason="保存短期对话与当前工作流摘要",
            ttl_seconds=int(timedelta(hours=24).total_seconds()),
        )
    ]
    if context.incident_id:
        decisions.append(
            MemoryDecision(
                tier=MemoryTier.INCIDENT,
                action=MemoryAction.WRITE,
                reason="事故事实写入 Postgres 权威记录",
            )
        )
    if context.workflow_completed:
        decisions.append(
            MemoryDecision(
                tier=MemoryTier.CANDIDATE,
                action=MemoryAction.WRITE,
                reason="诊断结果先进入候选经验区，不直接成为可信知识",
                ttl_seconds=int(timedelta(days=90).total_seconds()),
            )
        )
    can_promote = all(
        (
            context.workflow_completed,
            context.human_root_cause_confirmed,
            context.remediation_verified,
            context.incident_closed,
            context.redaction_passed,
        )
    )
    decisions.append(
        MemoryDecision(
            tier=MemoryTier.VERIFIED_KNOWLEDGE,
            action=MemoryAction.PROMOTE if can_promote else MemoryAction.SKIP,
            reason=(
                "根因、处置、恢复、关闭和脱敏均已确认"
                if can_promote
                else "未满足可信知识晋升的全部条件"
            ),
        )
    )
    return decisions


def retention_decision(
    *,
    tier: MemoryTier,
    age_days: int,
    last_used_days: int,
    confidence: float,
    contradicted: bool = False,
) -> MemoryDecision:
    if tier == MemoryTier.SESSION:
        return MemoryDecision(tier=tier, action=MemoryAction.DELETE, reason="会话 TTL 到期")
    if contradicted:
        return MemoryDecision(tier=tier, action=MemoryAction.ARCHIVE, reason="知识被人工反馈或新事实否定")
    if tier == MemoryTier.CANDIDATE and (age_days >= 90 or confidence < 0.5):
        return MemoryDecision(tier=tier, action=MemoryAction.DELETE, reason="候选经验过期或低可信")
    if tier == MemoryTier.VERIFIED_KNOWLEDGE and last_used_days >= 365:
        return MemoryDecision(tier=tier, action=MemoryAction.ARCHIVE, reason="长期未验证，进入复审")
    return MemoryDecision(tier=tier, action=MemoryAction.SKIP, reason="继续保留")

