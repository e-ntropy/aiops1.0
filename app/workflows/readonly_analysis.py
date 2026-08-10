"""只读优化与容量性能分析，共用真实系统快照且绝不执行建议。"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.workflows.capabilities import CapabilityId
from app.workflows.models import WorkflowState
from app.workflows.system_inspection import (
    HealthFinding,
    LocalInspectionResult,
    SystemCollector,
    SystemHealthSnapshot,
    execute_local_inspection,
)


class AnalysisDataSufficiency(StrEnum):
    CURRENT_SNAPSHOT = "current_snapshot"
    HISTORICAL_SERIES = "historical_series"
    INSUFFICIENT = "insufficient"


class ResourceHeadroom(BaseModel):
    resource: str
    used_percent: float = Field(ge=0, le=100)
    headroom_percent: float = Field(ge=0, le=100)
    status: str


class ReadonlyRecommendation(BaseModel):
    category: str
    title: str
    rationale: str
    evidence: list[str] = Field(default_factory=list)
    suggested_change: str = ""
    risk_level: str = Field(default="low", pattern="^(low|medium|high)$")
    execution_allowed: bool = False
    requires_human_confirmation: bool = False


class ReadonlySystemAnalysis(BaseModel):
    capability_id: CapabilityId
    data_sufficiency: AnalysisDataSufficiency
    forecast_available: bool = False
    baseline: dict[str, float] = Field(default_factory=dict)
    headroom: list[ResourceHeadroom] = Field(default_factory=list)
    bottlenecks: list[str] = Field(default_factory=list)
    recommendations: list[ReadonlyRecommendation] = Field(default_factory=list)
    required_next_data: list[str] = Field(default_factory=list)
    summary: str


class ReadonlyAnalysisResult(BaseModel):
    state: WorkflowState
    inspection: LocalInspectionResult
    analysis: ReadonlySystemAnalysis


def _headroom(resource: str, used: float) -> ResourceHeadroom:
    status = "critical" if used >= 90 else "warning" if used >= 80 else "healthy"
    return ResourceHeadroom(
        resource=resource,
        used_percent=round(used, 2),
        headroom_percent=round(max(0, 100 - used), 2),
        status=status,
    )


def analyze_system_snapshot(
    snapshot: SystemHealthSnapshot,
    findings: list[HealthFinding],
    *,
    capability_id: CapabilityId,
) -> ReadonlySystemAnalysis:
    if capability_id not in {
        CapabilityId.READONLY_OPTIMIZATION,
        CapabilityId.CAPACITY_PERFORMANCE,
    }:
        raise ValueError("只读分析器仅支持优化或容量性能能力")
    max_disk = max((disk.percent for disk in snapshot.disks), default=0)
    headroom = [
        _headroom("cpu", snapshot.cpu_percent),
        _headroom("memory", snapshot.memory_percent),
        _headroom("swap", snapshot.swap_percent),
        _headroom("disk_max", max_disk),
    ]
    baseline = {
        "cpu_percent": snapshot.cpu_percent,
        "memory_percent": snapshot.memory_percent,
        "swap_percent": snapshot.swap_percent,
        "max_disk_percent": max_disk,
    }
    bottlenecks = [finding.summary for finding in findings]
    recommendations: list[ReadonlyRecommendation] = []

    if snapshot.top_processes:
        top = snapshot.top_processes[0]
        recommendations.append(
            ReadonlyRecommendation(
                category="process",
                title="验证高占用进程是否符合业务预期",
                rationale=f"当前最高内存进程为 {top.name} (PID {top.pid}, RSS {top.rss_mb:.2f} MB)。",
                evidence=[f"pid={top.pid}", f"memory_percent={top.memory_percent:.2f}"],
                suggested_change="先采集该进程 24 小时资源趋势和业务负载关联，再决定是否调整。",
            )
        )
    if snapshot.memory_percent >= 80:
        recommendations.append(
            ReadonlyRecommendation(
                category="memory",
                title="评估内存上限或实例容量调整",
                rationale=f"内存使用率达到 {snapshot.memory_percent:.1f}%，当前余量较低。",
                evidence=[f"memory_percent={snapshot.memory_percent:.1f}"],
                suggested_change="人工评审应用内存上限、缓存策略或实例规格；本工作流不修改配置。",
                risk_level="medium",
                requires_human_confirmation=True,
            )
        )
    if max_disk >= 80:
        recommendations.append(
            ReadonlyRecommendation(
                category="disk",
                title="制定磁盘增长与保留策略",
                rationale=f"最高磁盘使用率达到 {max_disk:.1f}%。",
                evidence=[f"max_disk_percent={max_disk:.1f}"],
                suggested_change="人工核对日志保留、临时文件和扩容窗口；不自动删除文件。",
                risk_level="high",
                requires_human_confirmation=True,
            )
        )

    required_next_data = [
        "至少 7 天、建议 14-30 天的 CPU/内存/磁盘时间序列",
        "业务 QPS、并发数、P95/P99 延迟和错误率",
        "发布、批任务和流量峰值时间点",
    ]
    if capability_id == CapabilityId.CAPACITY_PERFORMANCE:
        summary = (
            "已计算当前资源余量；只有单次快照，不能可靠预测扩容时间或增长趋势。"
        )
    else:
        summary = (
            f"基于当前只读快照识别 {len(bottlenecks)} 个阈值瓶颈和 "
            f"{len(recommendations)} 条优化建议；未执行任何变更。"
        )
    return ReadonlySystemAnalysis(
        capability_id=capability_id,
        data_sufficiency=AnalysisDataSufficiency.CURRENT_SNAPSHOT,
        baseline=baseline,
        headroom=headroom,
        bottlenecks=bottlenecks,
        recommendations=recommendations,
        required_next_data=required_next_data,
        summary=summary,
    )


async def execute_readonly_analysis(
    state: WorkflowState,
    *,
    collector: SystemCollector | None = None,
) -> ReadonlyAnalysisResult:
    capability_id = CapabilityId(state.capability_id)
    if capability_id not in {
        CapabilityId.READONLY_OPTIMIZATION,
        CapabilityId.CAPACITY_PERFORMANCE,
    }:
        raise ValueError("当前 Capability 不能进入只读优化/容量分析器")
    inspection = await execute_local_inspection(
        state,
        collector=collector,
    )
    if inspection.snapshot is None:
        analysis = ReadonlySystemAnalysis(
            capability_id=capability_id,
            data_sufficiency=AnalysisDataSufficiency.INSUFFICIENT,
            required_next_data=["可用的系统健康快照"],
            summary="关键数据源不可用，无法生成基于事实的优化或容量结论。",
        )
        return ReadonlyAnalysisResult(state=inspection.state, inspection=inspection, analysis=analysis)
    analysis = analyze_system_snapshot(
        inspection.snapshot,
        inspection.findings,
        capability_id=capability_id,
    )
    inspection.state.outcome.summary = analysis.summary
    inspection.state.outcome.recommendations = [
        item.suggested_change for item in analysis.recommendations
    ]
    inspection.state.outcome.report_markdown += (
        "\n\n### 只读分析\n\n"
        + analysis.summary
        + "\n\n"
        + "\n".join(f"- {item.title}：{item.suggested_change}" for item in analysis.recommendations)
    )
    return ReadonlyAnalysisResult(
        state=inspection.state,
        inspection=inspection,
        analysis=analysis,
    )
