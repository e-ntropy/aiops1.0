"""产品级 Capability Registry：重组 Skill、Tool 与执行策略。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.tools.meta import get_meta
from app.workflows.models import QueryUnderstanding, RiskLevel, WorkflowIntent


class CapabilityId(StrEnum):
    KNOWLEDGE_QA = "knowledge_qa"
    SYSTEM_STATUS = "system_status"
    SYSTEM_INSPECTION = "system_inspection"
    ADAPTIVE_DIAGNOSIS = "adaptive_diagnosis"
    READONLY_OPTIMIZATION = "readonly_optimization"
    CAPACITY_PERFORMANCE = "capacity_performance"
    INCIDENT_REVIEW = "incident_review"
    EVALUATION = "evaluation"


class CapabilityDefinition(BaseModel):
    id: CapabilityId
    display_name: str
    description: str
    intents: list[WorkflowIntent]
    execution_strategy: str
    skill_candidates: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    read_only: bool = True
    requires_live_scope: bool = False
    supports_memory: bool = True
    fallback_strategy: str


class CapabilityPlan(BaseModel):
    capability: CapabilityDefinition
    selected_skills: list[str]
    allowed_tools: list[str]
    risk_level: RiskLevel
    reasons: list[str]


_CAPABILITIES = (
    CapabilityDefinition(
        id=CapabilityId.KNOWLEDGE_QA,
        display_name="运维知识问答",
        description="检索可信知识库并基于来源回答，不把知识当作现场证据。",
        intents=[WorkflowIntent.KNOWLEDGE_QA],
        execution_strategy="rag_stream",
        allowed_tools=["search_knowledge_base"],
        fallback_strategy="知识库不可用时明确未检索到来源，不生成现场结论",
    ),
    CapabilityDefinition(
        id=CapabilityId.SYSTEM_STATUS,
        display_name="系统状态查询",
        description="按需读取 CPU、内存、磁盘和后台进程当前状态。",
        intents=[WorkflowIntent.STATUS_QUERY],
        execution_strategy="scoped_readonly_snapshot",
        skill_candidates=["host_resource_diagnosis"],
        allowed_tools=[
            "get_system_health_snapshot",
            "get_local_cpu_memory",
            "get_local_disk_usage",
            "list_top_processes",
        ],
        requires_live_scope=True,
        fallback_strategy="有限重试后标记数据源不可用",
    ),
    CapabilityDefinition(
        id=CapabilityId.SYSTEM_INSPECTION,
        display_name="一键系统巡检",
        description="采集结构化系统快照、固定阈值判定并形成 Evidence。",
        intents=[WorkflowIntent.SYSTEM_INSPECTION],
        execution_strategy="full_readonly_inspection",
        skill_candidates=["host_resource_diagnosis", "generic_oncall"],
        allowed_tools=["get_system_health_snapshot"],
        requires_live_scope=True,
        fallback_strategy="关键快照失败则关闭式终止，不由模型补写",
    ),
    CapabilityDefinition(
        id=CapabilityId.ADAPTIVE_DIAGNOSIS,
        display_name="自适应故障诊断",
        description="Fast Triage 先收集最小证据，质量不足时保留证据升级 Deep。",
        intents=[WorkflowIntent.FAULT_DIAGNOSIS],
        execution_strategy="fast_triage_evidence_gate_deep",
        skill_candidates=[
            "host_resource_diagnosis",
            "network_diagnosis",
            "container_diagnosis",
            "generic_oncall",
        ],
        allowed_tools=[
            "search_knowledge_base",
            "get_system_health_snapshot",
            "query_windows_event",
            "ping_host",
            "http_check",
            "dns_lookup",
            "check_port",
            "docker_ps",
            "docker_stats",
            "docker_logs",
            "docker_inspect",
        ],
        requires_live_scope=True,
        fallback_strategy="Fast 失败或证据不足时升级 Deep；Deep 不可用则输出证据缺口",
    ),
    CapabilityDefinition(
        id=CapabilityId.READONLY_OPTIMIZATION,
        display_name="系统优化助手（只读）",
        description="基于真实快照识别优化机会，只生成建议和风险，不执行变更。",
        intents=[WorkflowIntent.OPTIMIZATION],
        execution_strategy="snapshot_then_readonly_advice",
        skill_candidates=["host_resource_diagnosis", "generic_oncall"],
        allowed_tools=["get_system_health_snapshot", "search_knowledge_base"],
        requires_live_scope=True,
        fallback_strategy="无快照时只返回通用检查清单并标记证据不足",
    ),
    CapabilityDefinition(
        id=CapabilityId.CAPACITY_PERFORMANCE,
        display_name="容量与性能分析",
        description="计算资源余量和瓶颈；缺历史序列时不伪造预测。",
        intents=[WorkflowIntent.CAPACITY_PERFORMANCE],
        execution_strategy="metrics_baseline_headroom_analysis",
        skill_candidates=["host_resource_diagnosis", "generic_oncall"],
        allowed_tools=[
            "get_system_health_snapshot",
            "prom_query",
            "prom_query_range",
        ],
        requires_live_scope=True,
        fallback_strategy="缺趋势数据时降级为当前余量分析并给出补数要求",
    ),
    CapabilityDefinition(
        id=CapabilityId.INCIDENT_REVIEW,
        display_name="事故复盘",
        description="基于事故事实还原时间线并形成行动项。",
        intents=[WorkflowIntent.INCIDENT_REVIEW],
        execution_strategy="incident_fact_review",
        skill_candidates=["generic_oncall"],
        fallback_strategy="缺少 incident_id 时二次确认",
    ),
    CapabilityDefinition(
        id=CapabilityId.EVALUATION,
        display_name="诊断评测",
        description="运行可复现的路由、检索与诊断评测。",
        intents=[WorkflowIntent.EVALUATION],
        execution_strategy="offline_evaluation",
        fallback_strategy="外部 Provider 未就绪时仅运行离线确定性评测",
    ),
)


def list_capabilities() -> list[CapabilityDefinition]:
    return [item.model_copy(deep=True) for item in _CAPABILITIES]


def plan_capability(query: QueryUnderstanding) -> CapabilityPlan:
    definition = next(
        (item for item in _CAPABILITIES if query.primary_intent in item.intents),
        _CAPABILITIES[0],
    ).model_copy(deep=True)
    # 双层只读硬墙：Capability 声明 + ToolMeta 均必须只读。
    safe_tools = [
        name for name in definition.allowed_tools if get_meta(name).effective_read_only({})
    ]
    reasons = [
        f"primary_intent={query.primary_intent.value}",
        f"strategy={definition.execution_strategy}",
    ]
    if len(safe_tools) != len(definition.allowed_tools):
        reasons.append("已移除未登记为只读的工具")
    return CapabilityPlan(
        capability=definition,
        selected_skills=list(definition.skill_candidates),
        allowed_tools=safe_tools,
        risk_level=query.risk_level,
        reasons=reasons,
    )
