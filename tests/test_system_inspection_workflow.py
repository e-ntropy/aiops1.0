from __future__ import annotations

import unittest

from app.workflows.models import WorkflowPhase
from app.workflows.orchestrator import prepare_workflow
from app.workflows.system_inspection import (
    DiskSnapshot,
    FindingSeverity,
    ProcessSnapshot,
    SystemHealthSnapshot,
    evaluate_snapshot,
    execute_local_inspection,
)


def _snapshot(*, cpu: float = 20, memory: float = 30, disk: float = 40) -> SystemHealthSnapshot:
    return SystemHealthSnapshot(
        os="TestOS",
        cpu_percent=cpu,
        memory_percent=memory,
        memory_used_gb=3,
        memory_available_gb=7,
        memory_total_gb=10,
        disks=[
            DiskSnapshot(
                device="test",
                mountpoint="/data",
                total_gb=100,
                used_gb=disk,
                free_gb=100 - disk,
                percent=disk,
            )
        ],
        top_processes=[
            ProcessSnapshot(
                pid=42,
                name="worker",
                memory_percent=12,
                rss_mb=512,
            )
        ],
    )


class FakeCollector:
    def __init__(self, snapshot: SystemHealthSnapshot, failures: int = 0) -> None:
        self.snapshot = snapshot
        self.failures = failures
        self.calls = 0

    def collect(self, process_limit: int = 10) -> SystemHealthSnapshot:
        self.calls += 1
        if self.calls <= self.failures:
            raise TimeoutError("temporary timeout")
        return self.snapshot


class AlwaysFailCollector:
    def collect(self, process_limit: int = 10) -> SystemHealthSnapshot:
        raise TimeoutError("source unavailable")


class InspectionEvaluationTests(unittest.TestCase):
    def test_thresholds_are_deterministic(self) -> None:
        findings = evaluate_snapshot(_snapshot(cpu=96, memory=85, disk=91))
        by_code = {item.code: item for item in findings}
        self.assertEqual(by_code["CPU_PRESSURE"].severity, FindingSeverity.CRITICAL)
        self.assertEqual(by_code["MEMORY_PRESSURE"].severity, FindingSeverity.WARNING)
        self.assertEqual(by_code["DISK_PRESSURE"].severity, FindingSeverity.CRITICAL)


class LocalInspectionWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_records_scoped_evidence_and_report(self) -> None:
        state = await prepare_workflow("巡检本机系统健康", use_llm=False)
        result = await execute_local_inspection(
            state,
            collector=FakeCollector(_snapshot(memory=92)),
            retry_sleep=lambda _: None,
        )
        self.assertEqual(result.state.phase, WorkflowPhase.COMPLETED)
        self.assertEqual(result.tool_result.status.value, "succeeded")
        self.assertEqual(result.state.evidence[0].scope.resource_id, "localhost")
        self.assertEqual(result.state.evidence[0].tool_call_id, result.tool_result.tool_call_id)
        self.assertTrue(result.state.outcome.requires_escalation)
        self.assertIn("升级故障诊断", result.state.outcome.next_action)
        recommendations = " ".join(result.state.outcome.recommendations)
        self.assertIn("不自动执行变更", recommendations)
        self.assertNotIn("已自动执行", recommendations)

    async def test_retry_is_audited_then_succeeds(self) -> None:
        state = await prepare_workflow("查看本机后台进程", use_llm=False)
        collector = FakeCollector(_snapshot(), failures=1)
        result = await execute_local_inspection(
            state,
            collector=collector,
            retry_sleep=lambda _: None,
        )
        self.assertEqual(collector.calls, 2)
        self.assertEqual(result.tool_result.attempts, 2)
        self.assertEqual(len(result.state.failures), 1)
        self.assertTrue(result.state.failures[0].retryable)

    async def test_required_source_failure_fails_closed(self) -> None:
        state = await prepare_workflow("查看本机后台进程", use_llm=False)
        state.budgets.max_tool_retries = 1
        result = await execute_local_inspection(
            state,
            collector=AlwaysFailCollector(),
            retry_sleep=lambda _: None,
        )
        self.assertEqual(result.state.phase, WorkflowPhase.FAILED)
        self.assertEqual(result.tool_result.status.value, "failed")
        self.assertIsNone(result.snapshot)
        self.assertEqual(len(result.state.failures), 2)

    async def test_remote_scope_is_rejected(self) -> None:
        state = await prepare_workflow("检查 10.0.0.8 当前状态", use_llm=False)
        state.phase = WorkflowPhase.READY
        with self.assertRaisesRegex(ValueError, "只允许"):
            await execute_local_inspection(state, collector=FakeCollector(_snapshot()))


if __name__ == "__main__":
    unittest.main()
