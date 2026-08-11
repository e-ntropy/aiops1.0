"""统一 AIOps Agent 的稳定工作流数据契约。

这些模型刻意与 LangGraph 节点解耦：API、同步工作流、后台 Worker 和评测都复用
同一套输入输出，避免内部诊断阶段各自维护不兼容的状态字段。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator


class WorkflowIntent(StrEnum):
    KNOWLEDGE_QA = "knowledge_qa"
    STATUS_QUERY = "status_query"
    SYSTEM_INSPECTION = "system_inspection"
    FAULT_DIAGNOSIS = "fault_diagnosis"
    OPTIMIZATION = "optimization"
    CAPACITY_PERFORMANCE = "capacity_performance"
    INCIDENT_REVIEW = "incident_review"
    EVALUATION = "evaluation"
    OUT_OF_SCOPE = "out_of_scope"


class RiskLevel(StrEnum):
    READ_ONLY = "read_only"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ScopeKind(StrEnum):
    NONE = "none"
    LOCAL_HOST = "local_host"
    REMOTE_HOST = "remote_host"
    SERVICE = "service"
    CONTAINER = "container"
    INCIDENT = "incident"


class ConfirmationStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    REQUIRED = "required"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


class EvidenceStatus(StrEnum):
    OBSERVED = "observed"
    REFERENCE = "reference"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


class WorkflowPhase(StrEnum):
    UNDERSTANDING = "understanding"
    CLARIFYING = "clarifying"
    SCOPING = "scoping"
    READY = "ready"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LifecycleStage(StrEnum):
    NOT_STARTED = "not_started"
    AWAITING_DIAGNOSIS_CONFIRMATION = "awaiting_diagnosis_confirmation"
    AWAITING_PLAN_CONFIRMATION = "awaiting_plan_confirmation"
    AWAITING_VERIFICATION = "awaiting_verification"
    RECOVERED = "recovered"
    NOT_RECOVERED = "not_recovered"
    INCONCLUSIVE = "inconclusive"
    CLOSED = "closed"


class HumanDecisionStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class DiagnosisConfirmation(BaseModel):
    status: HumanDecisionStatus = HumanDecisionStatus.PENDING
    proposed_root_cause: str = ""
    confirmed_root_cause: str = ""
    note: str = ""
    decided_at: datetime | None = None


class RemediationAction(BaseModel):
    id: str = Field(default_factory=lambda: f"act_{uuid4().hex[:12]}")
    title: str
    action_kind: str = Field(pattern="^(observe|verify|recommendation)$")
    description: str
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    execution_allowed: bool = False
    requires_human_confirmation: bool = False
    evidence_ids: list[str] = Field(default_factory=list)


class RemediationPlanState(BaseModel):
    status: HumanDecisionStatus = HumanDecisionStatus.PENDING
    actions: list[RemediationAction] = Field(default_factory=list)
    note: str = ""
    decided_at: datetime | None = None


class RecoveryVerificationState(BaseModel):
    status: LifecycleStage = LifecycleStage.NOT_STARTED
    baseline_evidence_id: str = ""
    verification_evidence_id: str = ""
    summary: str = ""
    checked_at: datetime | None = None


class IncidentClosureState(BaseModel):
    closed: bool = False
    closed_at: datetime | None = None
    closed_by: str = ""
    redaction_passed: bool = False
    eval_sample_id: str = ""


class IncidentLifecycleState(BaseModel):
    stage: LifecycleStage = LifecycleStage.NOT_STARTED
    diagnosis: DiagnosisConfirmation = Field(default_factory=DiagnosisConfirmation)
    remediation: RemediationPlanState = Field(default_factory=RemediationPlanState)
    verification: RecoveryVerificationState = Field(default_factory=RecoveryVerificationState)
    closure: IncidentClosureState = Field(default_factory=IncidentClosureState)


class QuerySubtask(BaseModel):
    id: str = Field(default_factory=lambda: f"sub_{uuid4().hex[:12]}")
    objective: str = Field(min_length=1, max_length=500)
    expected_output: str = Field(default="", max_length=500)
    requires_live_data: bool = False
    risk_level: RiskLevel = RiskLevel.READ_ONLY


class ClarificationTurn(BaseModel):
    question: str
    answer: str
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class QueryUnderstanding(BaseModel):
    raw_query: str = Field(min_length=1, max_length=4000)
    rewritten_query: str = Field(min_length=1, max_length=4000)
    primary_intent: WorkflowIntent
    secondary_intents: list[WorkflowIntent] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list, max_length=12)
    subtasks: list[QuerySubtask] = Field(default_factory=list, max_length=12)
    entities: dict[str, str] = Field(default_factory=dict)
    missing_information: list[str] = Field(default_factory=list, max_length=12)
    risk_level: RiskLevel = RiskLevel.READ_ONLY
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_confirmation: bool = False
    clarification_question: str = Field(default="", max_length=1000)
    via: str = Field(default="deterministic", max_length=40)

    @field_validator("secondary_intents")
    @classmethod
    def _deduplicate_secondary(
        cls, values: list[WorkflowIntent]
    ) -> list[WorkflowIntent]:
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def _confirmation_has_reason(self) -> QueryUnderstanding:
        if self.requires_confirmation and not (
            self.clarification_question or self.missing_information
        ):
            raise ValueError("requires_confirmation 必须说明缺失信息或确认问题")
        return self


class TargetScope(BaseModel):
    kind: ScopeKind = ScopeKind.NONE
    environment: str = Field(default="", max_length=100)
    resource_id: str = Field(default="", max_length=300)
    service: str = Field(default="", max_length=200)
    instance: str = Field(default="", max_length=300)
    time_start: datetime | None = None
    time_end: datetime | None = None
    validated: bool = False
    validation_errors: list[str] = Field(default_factory=list)
    source: str = Field(default="query", max_length=100)

    @model_validator(mode="after")
    def _time_range_is_ordered(self) -> TargetScope:
        if self.time_start and self.time_end and self.time_start > self.time_end:
            raise ValueError("time_start 不能晚于 time_end")
        if self.kind in {
            ScopeKind.REMOTE_HOST,
            ScopeKind.SERVICE,
            ScopeKind.CONTAINER,
        } and not (self.resource_id or self.service or self.instance):
            self.validation_errors.append("远程或服务作用域缺少资源标识")
            self.validated = False
        return self

    @property
    def allows_live_tools(self) -> bool:
        return self.validated and self.kind != ScopeKind.NONE


class EvidenceItem(BaseModel):
    id: str = Field(default_factory=lambda: f"ev_{uuid4().hex}")
    run_id: str = ""
    incident_id: str = ""
    source: str
    type: str
    status: EvidenceStatus = EvidenceStatus.OBSERVED
    summary: str = Field(min_length=1, max_length=4000)
    content: dict[str, Any] = Field(default_factory=dict)
    scope: TargetScope = Field(default_factory=TargetScope)
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tool_call_id: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _observed_evidence_requires_scope(self) -> EvidenceItem:
        if self.status == EvidenceStatus.OBSERVED and not self.scope.validated:
            raise ValueError("现场 observed Evidence 必须绑定已验证 TargetScope")
        return self


class FailureRecord(BaseModel):
    stage: str
    error_type: str
    message: str
    attempt: int = Field(default=1, ge=1)
    retryable: bool = False
    fallback_action: str = ""
    tool_name: str = ""
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ExecutionBudget(BaseModel):
    max_steps: int = Field(default=12, ge=1, le=100)
    max_clarification_rounds: int = Field(default=2, ge=0, le=5)
    max_tool_retries: int = Field(default=2, ge=0, le=5)
    max_total_tokens: int = Field(default=0, ge=0)
    max_total_ms: int = Field(default=0, ge=0)


class MemoryContext(BaseModel):
    session_refs: list[str] = Field(default_factory=list)
    incident_refs: list[str] = Field(default_factory=list)
    knowledge_refs: list[str] = Field(default_factory=list)
    profile_refs: list[str] = Field(default_factory=list)
    profile_items: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    recalled_items: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=20,
        description="由服务端按作用域召回的 Memory 摘要；Candidate 不得进入此列表。",
    )
    candidate_writes: list[dict[str, Any]] = Field(default_factory=list)
    read_decisions: list[dict[str, Any]] = Field(default_factory=list)
    write_decisions: list[dict[str, Any]] = Field(default_factory=list)
    retention_actions: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowOutcome(BaseModel):
    summary: str = ""
    report_markdown: str = ""
    recommendations: list[str] = Field(default_factory=list)
    root_cause: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    requires_escalation: bool = False
    next_action: str = ""


class WorkflowTransition(BaseModel):
    from_phase: WorkflowPhase
    to_phase: WorkflowPhase
    reason: str
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class WorkflowState(BaseModel):
    state_version: str = "2.0"
    revision: int = Field(
        default=0,
        ge=0,
        description="Postgres 乐观锁版本；0 表示尚未持久化。",
    )
    run_id: str = Field(default_factory=lambda: f"run_{uuid4().hex}")
    session_id: str = "default"
    incident_id: str = ""
    incident_group_id: str = ""
    capability_id: str = ""
    execution_strategy: str = ""
    selected_skills: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    phase: WorkflowPhase = WorkflowPhase.UNDERSTANDING
    query: QueryUnderstanding
    scope: TargetScope = Field(default_factory=TargetScope)
    confirmation_status: ConfirmationStatus = ConfirmationStatus.NOT_REQUIRED
    clarification_history: list[ClarificationTurn] = Field(default_factory=list)
    plan: list[QuerySubtask] = Field(default_factory=list)
    current_step: int = Field(default=0, ge=0)
    evidence: list[EvidenceItem] = Field(default_factory=list)
    failures: list[FailureRecord] = Field(default_factory=list)
    budgets: ExecutionBudget = Field(default_factory=ExecutionBudget)
    memory: MemoryContext = Field(default_factory=MemoryContext)
    outcome: WorkflowOutcome = Field(default_factory=WorkflowOutcome)
    lifecycle: IncidentLifecycleState = Field(default_factory=IncidentLifecycleState)
    transitions: list[WorkflowTransition] = Field(default_factory=list)
    terminal_reason: str = ""

    @model_validator(mode="after")
    def _raw_query_is_immutable_copy(self) -> WorkflowState:
        if not self.query.raw_query.strip():
            raise ValueError("raw_query 不能为空")
        if self.allowed_tools:
            from app.tools.meta import get_meta

            unsafe = [
                name
                for name in self.allowed_tools
                if not get_meta(name).effective_read_only({})
            ]
            if unsafe:
                raise ValueError(f"统一 Capability State 只允许只读工具: {unsafe}")
        return self
