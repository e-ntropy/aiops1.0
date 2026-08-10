"""本机只读巡检：结构化采集、确定性判定、证据与失败闭环。"""

from __future__ import annotations

import asyncio
import platform
from collections.abc import Callable
from datetime import datetime, timezone
from enum import StrEnum
from time import perf_counter
from typing import Protocol

import psutil
from pydantic import BaseModel, Field

from app.workflows.fallbacks import FailureAction, decide_failure_action
from app.workflows.models import (
    EvidenceItem,
    FailureRecord,
    ScopeKind,
    WorkflowIntent,
    WorkflowPhase,
    WorkflowState,
    WorkflowTransition,
)
from app.workflows.policies import can_execute_live_tools, validate_phase_transition
from app.workflows.tool_contracts import (
    ToolCallEnvelope,
    ToolExecutionResult,
    ToolExecutionStatus,
)

_GB = 1024**3


class FindingSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class DiskSnapshot(BaseModel):
    device: str
    mountpoint: str
    filesystem: str = ""
    total_gb: float = 0
    used_gb: float = 0
    free_gb: float = 0
    percent: float = Field(ge=0, le=100)


class ProcessSnapshot(BaseModel):
    pid: int
    name: str
    cpu_percent: float = 0
    memory_percent: float = 0
    rss_mb: float = 0


class SystemHealthSnapshot(BaseModel):
    host: str = "localhost"
    os: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    cpu_percent: float = Field(ge=0, le=100)
    physical_cores: int | None = None
    logical_cores: int | None = None
    memory_percent: float = Field(ge=0, le=100)
    memory_used_gb: float = 0
    memory_available_gb: float = 0
    memory_total_gb: float = 0
    swap_percent: float = Field(default=0, ge=0, le=100)
    disks: list[DiskSnapshot] = Field(default_factory=list)
    top_processes: list[ProcessSnapshot] = Field(default_factory=list)


class HealthFinding(BaseModel):
    code: str
    severity: FindingSeverity
    metric: str
    observed: float
    threshold: float
    summary: str
    recommendation: str


class LocalInspectionResult(BaseModel):
    state: WorkflowState
    snapshot: SystemHealthSnapshot | None = None
    findings: list[HealthFinding] = Field(default_factory=list)
    tool_result: ToolExecutionResult


class SystemCollector(Protocol):
    def collect(self, process_limit: int = 10) -> SystemHealthSnapshot: ...


class PsutilSystemCollector:
    """不读取环境变量和进程命令行，避免把凭据带入证据。"""

    def collect(self, process_limit: int = 10) -> SystemHealthSnapshot:
        memory = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disks: list[DiskSnapshot] = []
        for part in psutil.disk_partitions(all=False):
            try:
                usage = psutil.disk_usage(part.mountpoint)
            except (OSError, PermissionError):
                continue
            disks.append(
                DiskSnapshot(
                    device=part.device,
                    mountpoint=part.mountpoint,
                    filesystem=part.fstype,
                    total_gb=round(usage.total / _GB, 2),
                    used_gb=round(usage.used / _GB, 2),
                    free_gb=round(usage.free / _GB, 2),
                    percent=float(usage.percent),
                )
            )
        processes: list[ProcessSnapshot] = []
        for proc in psutil.process_iter(
            ["pid", "name", "cpu_percent", "memory_percent", "memory_info"]
        ):
            try:
                info = proc.info
                rss = getattr(info.get("memory_info"), "rss", 0)
                processes.append(
                    ProcessSnapshot(
                        pid=int(info["pid"]),
                        name=str(info.get("name") or "unknown"),
                        cpu_percent=round(float(info.get("cpu_percent") or 0), 2),
                        memory_percent=round(float(info.get("memory_percent") or 0), 2),
                        rss_mb=round(rss / 1024**2, 2),
                    )
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        processes.sort(key=lambda item: item.memory_percent, reverse=True)
        return SystemHealthSnapshot(
            os=f"{platform.system()} {platform.release()}",
            cpu_percent=float(psutil.cpu_percent(interval=0.2)),
            physical_cores=psutil.cpu_count(logical=False),
            logical_cores=psutil.cpu_count(logical=True),
            memory_percent=float(memory.percent),
            memory_used_gb=round(memory.used / _GB, 2),
            memory_available_gb=round(memory.available / _GB, 2),
            memory_total_gb=round(memory.total / _GB, 2),
            swap_percent=float(swap.percent),
            disks=disks,
            top_processes=processes[: max(1, min(process_limit, 30))],
        )


def evaluate_snapshot(snapshot: SystemHealthSnapshot) -> list[HealthFinding]:
    findings: list[HealthFinding] = []

    def add_pressure(metric: str, value: float, warning: float, critical: float, label: str) -> None:
        if value < warning:
            return
        severity = FindingSeverity.CRITICAL if value >= critical else FindingSeverity.WARNING
        threshold = critical if severity == FindingSeverity.CRITICAL else warning
        findings.append(
            HealthFinding(
                code=f"{metric.upper()}_PRESSURE",
                severity=severity,
                metric=metric,
                observed=value,
                threshold=threshold,
                summary=f"{label}使用率 {value:.1f}% 达到{severity.value}阈值 {threshold:.0f}%",
                recommendation=f"检查持续趋势和高占用进程，再决定是否调整{label}资源；当前不自动执行变更。",
            )
        )

    add_pressure("cpu", snapshot.cpu_percent, 80, 95, "CPU")
    add_pressure("memory", snapshot.memory_percent, 80, 90, "内存")
    add_pressure("swap", snapshot.swap_percent, 50, 80, "Swap")
    for disk in snapshot.disks:
        before = len(findings)
        add_pressure("disk", disk.percent, 80, 90, f"磁盘 {disk.mountpoint}")
        if len(findings) > before:
            findings[-1].code = "DISK_PRESSURE"
            findings[-1].metric = f"disk:{disk.mountpoint}"
    return findings


def _render_report(snapshot: SystemHealthSnapshot, findings: list[HealthFinding]) -> str:
    level = "异常" if findings else "未发现阈值异常"
    lines = [
        "## 本机只读巡检报告",
        "",
        f"- 结论：{level}",
        f"- CPU：{snapshot.cpu_percent:.1f}%",
        f"- 内存：{snapshot.memory_percent:.1f}%",
        f"- Swap：{snapshot.swap_percent:.1f}%",
        "",
        "### 结构化发现",
    ]
    if findings:
        lines.extend(f"- [{item.severity.value}] {item.summary}" for item in findings)
    else:
        lines.append("- 当前快照未越过固定阈值；单次快照不等同于长期健康证明。")
    lines.extend(["", "### 高内存进程"])
    lines.extend(
        f"- PID {item.pid} `{item.name}`：{item.rss_mb:.2f} MB ({item.memory_percent:.2f}%)"
        for item in snapshot.top_processes[:10]
    )
    return "\n".join(lines)


async def execute_local_inspection(
    state: WorkflowState,
    *,
    collector: SystemCollector | None = None,
    retry_sleep: Callable[[float], object] | None = None,
) -> LocalInspectionResult:
    if state.query.primary_intent not in {
        WorkflowIntent.STATUS_QUERY,
        WorkflowIntent.SYSTEM_INSPECTION,
        WorkflowIntent.OPTIMIZATION,
    }:
        raise ValueError("当前意图不属于系统状态或巡检")
    if state.scope.kind != ScopeKind.LOCAL_HOST:
        raise ValueError("MVP 只允许已确认的本机只读巡检")
    if state.phase != WorkflowPhase.READY:
        raise ValueError("本机巡检只能从 ready 阶段开始")
    allowed, reason = can_execute_live_tools(state)
    if not allowed:
        raise PermissionError(reason)
    validate_phase_transition(state.phase, WorkflowPhase.EXECUTING)
    state.phase = WorkflowPhase.EXECUTING
    state.transitions.append(
        WorkflowTransition(from_phase=WorkflowPhase.READY, to_phase=WorkflowPhase.EXECUTING, reason="local_read_only_inspection_started")
    )
    envelope = ToolCallEnvelope(
        run_id=state.run_id,
        tool_name="get_system_health_snapshot",
        arguments={"process_limit": 10},
        scope=state.scope,
        permission_decision="allow",
        max_retries=state.budgets.max_tool_retries,
    )
    collector = collector or PsutilSystemCollector()
    started = perf_counter()
    attempts = 0
    snapshot: SystemHealthSnapshot | None = None
    while snapshot is None:
        attempts += 1
        try:
            snapshot = await asyncio.to_thread(collector.collect, 10)
        except Exception as exc:  # noqa: BLE001 - 必须转为统一失败契约
            decision = decide_failure_action(
                error_type=type(exc).__name__,
                attempt=attempts,
                max_retries=envelope.max_retries,
                required_source=True,
            )
            state.failures.append(
                FailureRecord(
                    stage="system_inspection",
                    error_type=type(exc).__name__,
                    message=str(exc),
                    attempt=attempts,
                    retryable=decision.action == FailureAction.RETRY,
                    fallback_action=decision.action,
                    tool_name=envelope.tool_name,
                )
            )
            if decision.action == FailureAction.RETRY:
                delay = decision.retry_after_ms / 1000
                if retry_sleep is None:
                    await asyncio.sleep(delay)
                else:
                    maybe_awaitable = retry_sleep(delay)
                    if hasattr(maybe_awaitable, "__await__"):
                        await maybe_awaitable
                continue
            validate_phase_transition(state.phase, WorkflowPhase.FAILED)
            state.phase = WorkflowPhase.FAILED
            state.terminal_reason = decision.reason
            state.transitions.append(
                WorkflowTransition(from_phase=WorkflowPhase.EXECUTING, to_phase=WorkflowPhase.FAILED, reason=decision.action)
            )
            return LocalInspectionResult(
                state=state,
                tool_result=ToolExecutionResult(
                    tool_call_id=envelope.id,
                    status=ToolExecutionStatus.FAILED,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                    attempts=attempts,
                    elapsed_ms=int((perf_counter() - started) * 1000),
                ),
            )
    findings = evaluate_snapshot(snapshot)
    state.evidence.append(
        EvidenceItem(
            run_id=state.run_id,
            incident_id=state.incident_id,
            source="local_system",
            type="system_health_snapshot",
            summary=f"本机快照采集成功，发现 {len(findings)} 个阈值异常",
            content=snapshot.model_dump(mode="json"),
            scope=state.scope,
            tool_call_id=envelope.id,
        )
    )
    state.outcome.summary = "发现资源压力" if findings else "当前快照未发现阈值异常"
    state.outcome.report_markdown = _render_report(snapshot, findings)
    state.outcome.recommendations = list(dict.fromkeys(item.recommendation for item in findings))
    state.outcome.requires_escalation = any(item.severity == FindingSeverity.CRITICAL for item in findings)
    state.outcome.next_action = "升级故障诊断" if state.outcome.requires_escalation else "观察趋势或结束本次查询"
    state.outcome.confidence = 0.9
    validate_phase_transition(state.phase, WorkflowPhase.COMPLETED)
    state.phase = WorkflowPhase.COMPLETED
    state.transitions.append(
        WorkflowTransition(from_phase=WorkflowPhase.EXECUTING, to_phase=WorkflowPhase.COMPLETED, reason="structured_snapshot_and_evidence_recorded")
    )
    return LocalInspectionResult(
        state=state,
        snapshot=snapshot,
        findings=findings,
        tool_result=ToolExecutionResult(
            tool_call_id=envelope.id,
            status=ToolExecutionStatus.SUCCEEDED,
            output={"evidence_id": state.evidence[-1].id, "finding_count": len(findings)},
            attempts=attempts,
            elapsed_ms=int((perf_counter() - started) * 1000),
        ),
    )
