from __future__ import annotations

import unittest

from app.workflows.incident_lifecycle import (
    close_incident,
    confirm_diagnosis,
    confirm_remediation_plan,
    initialize_incident_lifecycle,
    verify_recovery,
)
from app.workflows.models import (
    EvidenceItem,
    HumanDecisionStatus,
    LifecycleStage,
    TargetScope,
    WorkflowPhase,
)
from app.workflows.orchestrator import prepare_workflow
from tests.test_system_inspection_workflow import FakeCollector, _snapshot


class IncidentLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def _diagnosed_state(self, *, with_baseline: bool = True):
        state = await prepare_workflow("本机内存异常，排查根因", use_llm=False)
        state.phase = WorkflowPhase.COMPLETED
        state.outcome.root_cause = "worker 进程内存持续增长"
        state.outcome.report_markdown = "# 诊断报告\n\n疑似 worker 内存压力。"
        state.outcome.recommendations = ["采集趋势后人工评估进程内存上限"]
        if with_baseline:
            baseline = _snapshot(memory=95)
            state.evidence.append(
                EvidenceItem(
                    run_id=state.run_id,
                    source="local_system",
                    type="system_health_snapshot",
                    summary="诊断基线内存 95%",
                    content=baseline.model_dump(mode="json"),
                    scope=state.scope,
                )
            )
        return state

    async def _confirmed_plan(self, *, with_baseline: bool = True):
        state = initialize_incident_lifecycle(
            await self._diagnosed_state(with_baseline=with_baseline)
        )
        state = confirm_diagnosis(
            state,
            decision=HumanDecisionStatus.CORRECTED,
            corrected_root_cause="worker 缓存未设置上限",
            note="人工结合发布记录确认",
        )
        return confirm_remediation_plan(
            state,
            decision=HumanDecisionStatus.CONFIRMED,
            note="仅授权新的只读快照验证",
        )

    async def test_full_close_loop_promotes_memory_and_builds_eval_sample(self) -> None:
        state = await verify_recovery(
            await self._confirmed_plan(),
            collector=FakeCollector(_snapshot(memory=60)),
        )
        self.assertEqual(state.lifecycle.stage, LifecycleStage.RECOVERED)
        result = close_incident(
            state,
            closed_by="oncall-user",
            redacted_query="本机某 worker 出现内存压力",
            redaction_passed=True,
        )
        self.assertEqual(result.state.lifecycle.stage, LifecycleStage.CLOSED)
        self.assertEqual(result.eval_sample.expected_root_cause, "worker 缓存未设置上限")
        self.assertEqual(result.eval_sample.review_status, "candidate")
        self.assertEqual(result.eval_sample.dataset_split, "quarantine")
        verified = result.state.memory.write_decisions[-1]
        self.assertEqual(verified["tier"], "verified_knowledge")
        self.assertEqual(verified["action"], "promote")

    async def test_critical_verification_cannot_close(self) -> None:
        state = await verify_recovery(
            await self._confirmed_plan(),
            collector=FakeCollector(_snapshot(memory=94)),
        )
        self.assertEqual(state.lifecycle.stage, LifecycleStage.NOT_RECOVERED)
        with self.assertRaisesRegex(ValueError, "恢复验证成功"):
            close_incident(
                state,
                closed_by="oncall-user",
                redacted_query="redacted",
                redaction_passed=True,
            )

    async def test_missing_baseline_is_inconclusive(self) -> None:
        state = await verify_recovery(
            await self._confirmed_plan(with_baseline=False),
            collector=FakeCollector(_snapshot(memory=40)),
        )
        self.assertEqual(state.lifecycle.stage, LifecycleStage.INCONCLUSIVE)
        self.assertIn("缺少", state.lifecycle.verification.summary)

    async def test_baseline_from_another_scope_is_not_used(self) -> None:
        state = await self._confirmed_plan()
        state.evidence[0].scope = TargetScope(
            kind="local_host",
            resource_id="another-host",
            validated=True,
            source="test",
        )
        state = await verify_recovery(
            state,
            collector=FakeCollector(_snapshot(memory=40)),
        )
        self.assertEqual(state.lifecycle.stage, LifecycleStage.INCONCLUSIVE)
        self.assertFalse(state.lifecycle.verification.baseline_evidence_id)

    async def test_collector_failure_is_recorded_and_inconclusive(self) -> None:
        class FailingCollector:
            def collect(self, process_limit: int = 10):
                raise ConnectionError("collector offline")

        state = await self._confirmed_plan()
        state.budgets.max_tool_retries = 1
        state = await verify_recovery(
            state,
            collector=FailingCollector(),
            retry_sleep=lambda _delay: None,
        )
        self.assertEqual(state.lifecycle.stage, LifecycleStage.INCONCLUSIVE)
        self.assertEqual(len(state.failures), 2)
        self.assertTrue(state.failures[0].retryable)
        self.assertFalse(state.failures[1].retryable)
        self.assertIn("不能仅凭模型文本", state.lifecycle.verification.summary)

    async def test_rejected_diagnosis_routes_to_more_evidence(self) -> None:
        state = initialize_incident_lifecycle(await self._diagnosed_state())
        state = confirm_diagnosis(
            state,
            decision=HumanDecisionStatus.REJECTED,
            note="证据与发布记录冲突",
        )
        self.assertEqual(state.lifecycle.stage, LifecycleStage.NOT_RECOVERED)
        self.assertIn("重新诊断", state.outcome.next_action)

    async def test_redaction_is_required_for_closure(self) -> None:
        state = await verify_recovery(
            await self._confirmed_plan(),
            collector=FakeCollector(_snapshot(memory=60)),
        )
        with self.assertRaisesRegex(ValueError, "脱敏"):
            close_incident(
                state,
                closed_by="oncall-user",
                redacted_query="raw secret host",
                redaction_passed=False,
            )


if __name__ == "__main__":
    unittest.main()
