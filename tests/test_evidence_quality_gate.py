from __future__ import annotations

import unittest

from app.workflows.evidence_quality import EvidenceGateDecision, assess_evidence_quality
from app.workflows.models import EvidenceItem, EvidenceStatus, WorkflowPhase
from app.workflows.orchestrator import prepare_workflow
from app.workflows.system_inspection import execute_local_inspection
from tests.test_system_inspection_workflow import FakeCollector, _snapshot


class EvidenceQualityGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_healthy_status_snapshot_can_complete(self) -> None:
        state = await prepare_workflow("查看本机后台进程", use_llm=False)
        inspected = await execute_local_inspection(
            state,
            collector=FakeCollector(_snapshot()),
            retry_sleep=lambda _: None,
        )
        result = assess_evidence_quality(inspected.state)
        self.assertEqual(result.decision, EvidenceGateDecision.COMPLETE)
        self.assertGreaterEqual(result.score, 0.7)

    async def test_critical_snapshot_escalates_and_preserves_parent_evidence(self) -> None:
        state = await prepare_workflow("巡检本机系统健康", use_llm=False)
        inspected = await execute_local_inspection(
            state,
            collector=FakeCollector(_snapshot(memory=95)),
            retry_sleep=lambda _: None,
        )
        result = assess_evidence_quality(inspected.state)
        self.assertEqual(result.decision, EvidenceGateDecision.ESCALATE_DEEP)
        self.assertEqual(result.escalation.parent_run_id, inspected.state.run_id)
        self.assertEqual(
            result.escalation.preserve_evidence_ids,
            [inspected.state.evidence[0].id],
        )

    async def test_diagnosis_without_live_evidence_collects_more_first(self) -> None:
        state = await prepare_workflow("本机内存异常，帮我排查根因", use_llm=False)
        state.phase = WorkflowPhase.EXECUTING
        result = assess_evidence_quality(state)
        self.assertEqual(result.decision, EvidenceGateDecision.COLLECT_MORE)

    async def test_scope_mismatch_is_blocked(self) -> None:
        state = await prepare_workflow("查看本机后台进程", use_llm=False)
        foreign_scope = state.scope.model_copy(update={"resource_id": "other-host"})
        state.evidence.append(
            EvidenceItem(
                source="metric",
                type="snapshot",
                summary="foreign host cpu",
                scope=foreign_scope,
            )
        )
        result = assess_evidence_quality(state)
        self.assertEqual(result.decision, EvidenceGateDecision.BLOCKED)

    async def test_majority_errors_requests_alternative_source(self) -> None:
        state = await prepare_workflow("查看本机后台进程", use_llm=False)
        state.evidence.append(
            EvidenceItem(
                source="metric",
                type="snapshot",
                summary="cpu observed",
                scope=state.scope,
            )
        )
        state.evidence.extend(
            [
                EvidenceItem(
                    source=f"source-{index}",
                    type="snapshot",
                    status=EvidenceStatus.ERROR,
                    summary="source failed",
                )
                for index in range(2)
            ]
        )
        result = assess_evidence_quality(state)
        self.assertEqual(result.decision, EvidenceGateDecision.COLLECT_MORE)


if __name__ == "__main__":
    unittest.main()
