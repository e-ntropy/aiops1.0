"""统一 AIOps Agent 工作流 API。"""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.schemas.common import ApiResponse
from app.workflows.adaptive_diagnosis import stream_adaptive_diagnosis
from app.workflows.background_policy import (
    BackgroundExecutionDecision,
    assess_background_execution,
)
from app.workflows.capabilities import CapabilityDefinition, list_capabilities
from app.workflows.capability_executor import stream_capability_workflow
from app.workflows.clarification import apply_clarification
from app.workflows.evidence_quality import (
    EvidenceQualityAssessment,
    assess_evidence_quality,
)
from app.workflows.incident_lifecycle import (
    IncidentClosureResult,
    close_incident,
    confirm_diagnosis,
    confirm_remediation_plan,
    initialize_incident_lifecycle,
    verify_recovery,
)
from app.workflows.models import HumanDecisionStatus, WorkflowState
from app.workflows.orchestrator import prepare_workflow
from app.workflows.readonly_analysis import (
    ReadonlyAnalysisResult,
    execute_readonly_analysis,
)
from app.workflows.repository import (
    WorkflowConflictError,
    WorkflowLeaseError,
    WorkflowNotFoundError,
    workflow_repository,
)
from app.workflows.system_inspection import (
    LocalInspectionResult,
    execute_local_inspection,
)

router = APIRouter(prefix="/workflows", tags=["workflows-v2"])


@router.get("/capabilities", summary="列出统一 AIOps 产品能力及复用边界")
async def capabilities() -> ApiResponse[list[CapabilityDefinition]]:
    return ApiResponse.success(list_capabilities())


class WorkflowPrepareRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(default="default", max_length=200)
    use_llm: bool = Field(
        default=True,
        description="LLM 不可用或结构化失败时自动降级到确定性理解",
    )
    actor: str = Field(default="user", min_length=1, max_length=200)


class StoredWorkflowRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)


async def _load_expected(body: StoredWorkflowRequest) -> WorkflowState:
    try:
        state = await workflow_repository.get(body.run_id)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workflow not found") from exc
    if state.revision != body.expected_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "workflow_revision_conflict",
                "expected": body.expected_revision,
                "actual": state.revision,
            },
        )
    return state


def _conflict(exc: WorkflowConflictError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "error": "workflow_revision_conflict",
            "expected": exc.expected,
            "actual": exc.actual,
        },
    )


async def _claim_expected(
    body: StoredWorkflowRequest,
    *,
    actor: str,
) -> WorkflowState:
    state = await _load_expected(body)
    try:
        return await workflow_repository.claim_execution(
            state,
            expected_revision=body.expected_revision,
            actor=actor,
        )
    except WorkflowConflictError as exc:
        raise _conflict(exc) from exc
    except WorkflowLeaseError as exc:
        raise HTTPException(
            status_code=409,
            detail={"error": "workflow_execution_leased", "run_id": exc.run_id},
        ) from exc


class WorkflowClarifyRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)
    answer: str = Field(min_length=1, max_length=2000)
    use_llm: bool = True
    actor: str = Field(default="user", min_length=1, max_length=200)


@router.post("/prepare", summary="理解请求、拆分任务并解析目标作用域")
async def prepare(body: WorkflowPrepareRequest) -> ApiResponse[WorkflowState]:
    state = await prepare_workflow(
        body.query,
        session_id=body.session_id,
        use_llm=body.use_llm,
    )
    state = await workflow_repository.create(state, actor=body.actor)
    message = "需要用户确认" if state.query.requires_confirmation or not state.scope.validated else "工作流已准备"
    return ApiResponse.success(state, message=message)


@router.post("/clarify", summary="提交二次确认并重新理解请求与作用域")
async def clarify(body: WorkflowClarifyRequest) -> ApiResponse[WorkflowState]:
    current = await _load_expected(body)
    state = await apply_clarification(
        current,
        body.answer,
        use_llm=body.use_llm,
    )
    try:
        state = await workflow_repository.save(
            state,
            expected_revision=body.expected_revision,
            event_type="clarification_applied",
            actor=body.actor,
        )
    except WorkflowConflictError as exc:
        raise _conflict(exc) from exc
    message = "仍需补充信息" if state.phase.value == "clarifying" else "确认完成，工作流已准备"
    return ApiResponse.success(state, message=message)


class LocalInspectionRequest(StoredWorkflowRequest):
    actor: str = Field(default="system", min_length=1, max_length=200)


@router.post(
    "/execute-local-inspection",
    summary="执行已确认作用域的本机只读系统巡检",
)
async def execute_inspection(
    body: LocalInspectionRequest,
) -> ApiResponse[LocalInspectionResult]:
    claimed = await _claim_expected(body, actor=body.actor)
    result = await execute_local_inspection(claimed)
    try:
        result.state = await workflow_repository.save(
            result.state,
            expected_revision=claimed.revision,
            event_type="system_inspection_finished",
            actor=body.actor,
        )
    except WorkflowConflictError as exc:
        raise _conflict(exc) from exc
    message = "巡检完成" if result.state.phase.value == "completed" else "关键数据源不可用"
    return ApiResponse.success(result, message=message)


class EvidenceQualityRequest(StoredWorkflowRequest):
    anomaly_detected: bool | None = None
    root_cause_confidence: float | None = Field(default=None, ge=0, le=1)


@router.post("/assess-evidence", summary="评估证据质量并决定是否启动专业 Agent 协作")
async def assess_evidence(
    body: EvidenceQualityRequest,
) -> ApiResponse[EvidenceQualityAssessment]:
    state = await _load_expected(body)
    assessment = assess_evidence_quality(
        state,
        anomaly_detected=body.anomaly_detected,
        root_cause_confidence=body.root_cause_confidence,
    )
    return ApiResponse.success(assessment, message=f"证据路由: {assessment.decision.value}")


class ReadonlyAnalysisRequest(StoredWorkflowRequest):
    actor: str = Field(default="system", min_length=1, max_length=200)


@router.post(
    "/execute-readonly-analysis",
    summary="执行系统优化助手或容量性能分析（只读）",
)
async def readonly_analysis(
    body: ReadonlyAnalysisRequest,
) -> ApiResponse[ReadonlyAnalysisResult]:
    claimed = await _claim_expected(body, actor=body.actor)
    result = await execute_readonly_analysis(claimed)
    try:
        result.state = await workflow_repository.save(
            result.state,
            expected_revision=claimed.revision,
            event_type="readonly_analysis_finished",
            actor=body.actor,
        )
    except WorkflowConflictError as exc:
        raise _conflict(exc) from exc
    return ApiResponse.success(result, message=result.analysis.summary)


class AdaptiveDiagnosisRequest(StoredWorkflowRequest):
    actor: str = Field(default="agent", min_length=1, max_length=200)


@router.post(
    "/adaptive-diagnosis/stream",
    summary="执行初步取证与按证据质量启动专业 Agent 的自适应诊断",
)
async def adaptive_diagnosis_stream(
    body: AdaptiveDiagnosisRequest,
) -> EventSourceResponse:
    # 在响应头发出前完成 CAS 与 Lease claim，确保冲突能以 HTTP 409 返回，
    # 而不是在 SSE 已经变成 200 后才从生成器内部抛错。
    state = await _claim_expected(body, actor=body.actor)
    claimed_revision = state.revision

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        try:
            async for event in stream_adaptive_diagnosis(state):
                yield {
                    "event": "message",
                    "data": json.dumps(event, ensure_ascii=False),
                }
            persisted_state = await workflow_repository.save(
                state,
                expected_revision=claimed_revision,
                event_type="adaptive_diagnosis_finished",
                actor=body.actor,
            )
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "workflow_persisted",
                        "message": "诊断状态已持久化",
                        "data": {"state": persisted_state.model_dump(mode="json")},
                    },
                    ensure_ascii=False,
                ),
            }
        except WorkflowConflictError as exc:
            raise _conflict(exc) from exc
        except asyncio.CancelledError as exc:
            await asyncio.shield(
                workflow_repository.record_execution_failure(
                    state,
                    expected_revision=claimed_revision,
                    actor=body.actor,
                    error=exc,
                    cancelled=True,
                )
            )
            raise
        except Exception as exc:  # noqa: BLE001 - 转为持久化失败事实
            failure_state = await workflow_repository.record_execution_failure(
                state,
                expected_revision=claimed_revision,
                actor=body.actor,
                error=exc,
            )
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "workflow_failed",
                        "message": failure_state.terminal_reason,
                        "data": {"state": failure_state.model_dump(mode="json")},
                    },
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(event_generator())


class CapabilityExecuteRequest(StoredWorkflowRequest):
    actor: str = Field(default="agent", min_length=1, max_length=200)


@router.post(
    "/background-eligibility",
    summary="检查统一工作流是否可安全进入后台队列",
)
async def background_eligibility(
    body: CapabilityExecuteRequest,
) -> ApiResponse[BackgroundExecutionDecision]:
    decision = assess_background_execution(await _load_expected(body))
    message = "允许提交后台任务" if decision.can_submit else "当前禁止提交后台任务"
    return ApiResponse.success(decision, message=message)


@router.post(
    "/execute/stream",
    summary="统一执行知识、状态、巡检、诊断、优化或容量能力",
)
async def execute_capability_stream(
    body: CapabilityExecuteRequest,
) -> EventSourceResponse:
    state = await _claim_expected(body, actor=body.actor)
    claimed_revision = state.revision

    async def event_generator() -> AsyncIterator[dict[str, str]]:
        try:
            async for event in stream_capability_workflow(state):
                yield {
                    "event": "message",
                    "data": json.dumps(event, ensure_ascii=False),
                }
            persisted_state = await workflow_repository.save(
                state,
                expected_revision=claimed_revision,
                event_type="capability_execution_finished",
                actor=body.actor,
            )
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "workflow_persisted",
                        "message": "工作流状态已持久化",
                        "data": {"state": persisted_state.model_dump(mode="json")},
                    },
                    ensure_ascii=False,
                ),
            }
        except WorkflowConflictError as exc:
            raise _conflict(exc) from exc
        except asyncio.CancelledError as exc:
            await asyncio.shield(
                workflow_repository.record_execution_failure(
                    state,
                    expected_revision=claimed_revision,
                    actor=body.actor,
                    error=exc,
                    cancelled=True,
                )
            )
            raise
        except Exception as exc:  # noqa: BLE001 - 转为持久化失败事实
            failure_state = await workflow_repository.record_execution_failure(
                state,
                expected_revision=claimed_revision,
                actor=body.actor,
                error=exc,
            )
            yield {
                "event": "message",
                "data": json.dumps(
                    {
                        "type": "workflow_failed",
                        "message": failure_state.terminal_reason,
                        "data": {"state": failure_state.model_dump(mode="json")},
                    },
                    ensure_ascii=False,
                ),
            }

    return EventSourceResponse(event_generator())


class LifecycleStateRequest(StoredWorkflowRequest):
    actor: str = Field(default="user", min_length=1, max_length=200)


@router.post("/lifecycle/initialize", summary="从已完成诊断初始化事故闭环")
async def lifecycle_initialize(
    body: LifecycleStateRequest,
) -> ApiResponse[WorkflowState]:
    state = initialize_incident_lifecycle(await _load_expected(body))
    try:
        state = await workflow_repository.save(
            state,
            expected_revision=body.expected_revision,
            event_type="incident_lifecycle_initialized",
            actor=body.actor,
        )
    except WorkflowConflictError as exc:
        raise _conflict(exc) from exc
    return ApiResponse.success(state, message="等待人工确认诊断结论")


class DiagnosisConfirmRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)
    decision: HumanDecisionStatus
    corrected_root_cause: str = Field(default="", max_length=2000)
    note: str = Field(default="", max_length=2000)
    decided_by: str = Field(min_length=1, max_length=200)


@router.post("/lifecycle/confirm-diagnosis", summary="人工确认、纠正或驳回诊断结论")
async def lifecycle_confirm_diagnosis(
    body: DiagnosisConfirmRequest,
) -> ApiResponse[WorkflowState]:
    current = await _load_expected(body)
    state = confirm_diagnosis(
        current,
        decision=body.decision,
        corrected_root_cause=body.corrected_root_cause,
        note=body.note,
    )
    try:
        state = await workflow_repository.save_human_decision(
            state,
            expected_revision=body.expected_revision,
            decision_type="diagnosis",
            status=body.decision.value,
            actor=body.decided_by,
            note=body.note,
            payload={"corrected_root_cause": body.corrected_root_cause},
        )
    except WorkflowConflictError as exc:
        raise _conflict(exc) from exc
    return ApiResponse.success(state, message=f"诊断决策: {body.decision.value}")


class PlanConfirmRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)
    decision: HumanDecisionStatus
    note: str = Field(default="", max_length=2000)
    decided_by: str = Field(min_length=1, max_length=200)


@router.post("/lifecycle/confirm-plan", summary="人工确认或驳回只读验证/建议计划")
async def lifecycle_confirm_plan(
    body: PlanConfirmRequest,
) -> ApiResponse[WorkflowState]:
    state = confirm_remediation_plan(
        await _load_expected(body),
        decision=body.decision,
        note=body.note,
    )
    try:
        state = await workflow_repository.save_human_decision(
            state,
            expected_revision=body.expected_revision,
            decision_type="remediation_plan",
            status=body.decision.value,
            actor=body.decided_by,
            note=body.note,
        )
    except WorkflowConflictError as exc:
        raise _conflict(exc) from exc
    return ApiResponse.success(state, message=f"计划决策: {body.decision.value}")


@router.post("/lifecycle/verify-recovery", summary="采集新快照并验证是否恢复")
async def lifecycle_verify_recovery(
    body: LifecycleStateRequest,
) -> ApiResponse[WorkflowState]:
    state = await verify_recovery(await _load_expected(body))
    try:
        state = await workflow_repository.save(
            state,
            expected_revision=body.expected_revision,
            event_type="recovery_verification_finished",
            actor=body.actor,
            payload={"status": state.lifecycle.verification.status.value},
        )
    except WorkflowConflictError as exc:
        raise _conflict(exc) from exc
    return ApiResponse.success(state, message=state.lifecycle.verification.summary)


class IncidentCloseRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=100)
    expected_revision: int = Field(ge=1)
    closed_by: str = Field(min_length=1, max_length=200)
    redacted_query: str = Field(min_length=1, max_length=4000)
    redaction_passed: bool = False


@router.post("/lifecycle/close", summary="关闭已恢复事故并生成脱敏评测样本")
async def lifecycle_close(
    body: IncidentCloseRequest,
) -> ApiResponse[IncidentClosureResult]:
    current = await _load_expected(body)
    result = close_incident(
        current,
        closed_by=body.closed_by,
        redacted_query=body.redacted_query,
        redaction_passed=body.redaction_passed,
    )
    try:
        result = await workflow_repository.close_with_learning(
            result,
            expected_revision=body.expected_revision,
            actor=body.closed_by,
        )
    except WorkflowConflictError as exc:
        raise _conflict(exc) from exc
    return ApiResponse.success(result, message="事故已关闭并生成评测样本")


@router.get("/{run_id}", summary="读取 Postgres 中的统一 Workflow 状态")
async def get_workflow(run_id: str) -> ApiResponse[WorkflowState]:
    try:
        state = await workflow_repository.get(run_id)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workflow not found") from exc
    return ApiResponse.success(state)


@router.get("/{run_id}/events", summary="读取 Workflow 追加式审计事件")
async def get_workflow_events(run_id: str) -> ApiResponse[list[dict[str, object]]]:
    try:
        await workflow_repository.get(run_id)
    except WorkflowNotFoundError as exc:
        raise HTTPException(status_code=404, detail="workflow not found") from exc
    return ApiResponse.success(await workflow_repository.list_events(run_id))
