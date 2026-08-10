"""AIOps V2 统一工作流准备 API。"""

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.schemas.common import ApiResponse
from app.workflows.clarification import apply_clarification
from app.workflows.evidence_quality import (
    EvidenceQualityAssessment,
    assess_evidence_quality,
)
from app.workflows.models import WorkflowState
from app.workflows.orchestrator import prepare_workflow
from app.workflows.system_inspection import (
    LocalInspectionResult,
    execute_local_inspection,
)

router = APIRouter(prefix="/workflows", tags=["workflows-v2"])


class WorkflowPrepareRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    session_id: str = Field(default="default", max_length=200)
    use_llm: bool = Field(
        default=True,
        description="LLM 不可用或结构化失败时自动降级到确定性理解",
    )


class WorkflowClarifyRequest(BaseModel):
    state: WorkflowState
    answer: str = Field(min_length=1, max_length=2000)
    use_llm: bool = True


@router.post("/prepare", summary="理解请求、拆分任务并解析目标作用域")
async def prepare(body: WorkflowPrepareRequest) -> ApiResponse[WorkflowState]:
    state = await prepare_workflow(
        body.query,
        session_id=body.session_id,
        use_llm=body.use_llm,
    )
    message = "需要用户确认" if state.query.requires_confirmation or not state.scope.validated else "工作流已准备"
    return ApiResponse.success(state, message=message)


@router.post("/clarify", summary="提交二次确认并重新理解请求与作用域")
async def clarify(body: WorkflowClarifyRequest) -> ApiResponse[WorkflowState]:
    state = await apply_clarification(
        body.state,
        body.answer,
        use_llm=body.use_llm,
    )
    message = "仍需补充信息" if state.phase.value == "clarifying" else "确认完成，工作流已准备"
    return ApiResponse.success(state, message=message)


class LocalInspectionRequest(BaseModel):
    state: WorkflowState


@router.post(
    "/execute-local-inspection",
    summary="执行已确认作用域的本机只读系统巡检",
)
async def execute_inspection(
    body: LocalInspectionRequest,
) -> ApiResponse[LocalInspectionResult]:
    result = await execute_local_inspection(body.state)
    message = "巡检完成" if result.state.phase.value == "completed" else "关键数据源不可用"
    return ApiResponse.success(result, message=message)


class EvidenceQualityRequest(BaseModel):
    state: WorkflowState
    anomaly_detected: bool | None = None
    root_cause_confidence: float | None = Field(default=None, ge=0, le=1)


@router.post("/assess-evidence", summary="评估证据质量并决定是否升级 Deep 诊断")
async def assess_evidence(
    body: EvidenceQualityRequest,
) -> ApiResponse[EvidenceQualityAssessment]:
    assessment = assess_evidence_quality(
        body.state,
        anomaly_detected=body.anomaly_detected,
        root_cause_confidence=body.root_cause_confidence,
    )
    return ApiResponse.success(assessment, message=f"证据路由: {assessment.decision.value}")
