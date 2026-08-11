from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.workflows.clarification import ClarificationLimitReached, apply_clarification
from app.workflows.fallbacks import FailureAction, decide_failure_action
from app.workflows.memory_policy import (
    MemoryAction,
    MemoryTier,
    MemoryWriteContext,
    decide_memory_reads,
    decide_memory_writes,
)
from app.workflows.models import (
    ConfirmationStatus,
    EvidenceItem,
    EvidenceStatus,
    ScopeKind,
    TargetScope,
    WorkflowIntent,
    WorkflowPhase,
)
from app.workflows.orchestrator import prepare_workflow
from app.workflows.policies import can_execute_live_tools, validate_phase_transition
from app.workflows.query_understanding import deterministic_understanding
from app.workflows.tool_contracts import ToolCallEnvelope


class QueryUnderstandingTests(unittest.IsolatedAsyncioTestCase):
    def test_local_process_query_is_status_query(self) -> None:
        result = deterministic_understanding("查看本机后台进程和内存占用")
        self.assertEqual(result.primary_intent, WorkflowIntent.STATUS_QUERY)
        self.assertFalse(result.requires_confirmation)
        self.assertEqual(result.raw_query, "查看本机后台进程和内存占用")

    def test_remote_live_query_requires_confirmation(self) -> None:
        result = deterministic_understanding("帮我检查生产 Redis 当前状态")
        self.assertTrue(result.requires_confirmation)
        self.assertIn("目标环境或资源", result.missing_information)

    def test_write_request_is_high_risk(self) -> None:
        result = deterministic_understanding("停止本机占用最高的进程")
        self.assertTrue(result.requires_confirmation)

    def test_embedded_configuration_change_is_high_risk(self) -> None:
        result = deterministic_understanding("修改本机 Nginx 配置并排查 502")
        self.assertEqual(result.primary_intent, WorkflowIntent.FAULT_DIAGNOSIS)
        self.assertEqual(result.risk_level.value, "high")
        self.assertTrue(result.requires_confirmation)

    def test_cleanup_and_process_termination_are_high_risk(self) -> None:
        for query in (
            "清理本机缓存并排查异常",
            "结束本机占用内存最高的进程并排查 OOM",
        ):
            with self.subTest(query=query):
                result = deterministic_understanding(query)
                self.assertEqual(result.risk_level.value, "high")
                self.assertTrue(result.requires_confirmation)
        self.assertEqual(result.risk_level.value, "high")

    def test_explanatory_fault_term_stays_knowledge(self) -> None:
        result = deterministic_understanding("OOM 是什么，为什么会发生？")
        self.assertEqual(result.primary_intent, WorkflowIntent.KNOWLEDGE_QA)

    def test_evaluation_beats_generic_live_word(self) -> None:
        result = deterministic_understanding("运行离线 benchmark 回归测试")
        self.assertEqual(result.primary_intent, WorkflowIntent.EVALUATION)

    def test_destructive_only_request_routes_to_readonly_optimization_gate(self) -> None:
        result = deterministic_understanding("删除本机所有临时文件并停止相关进程")
        self.assertEqual(result.primary_intent, WorkflowIntent.OPTIMIZATION)
        self.assertEqual(result.risk_level.value, "high")
        self.assertTrue(result.requires_confirmation)

    async def test_out_of_scope_request_fails_closed(self) -> None:
        state = await prepare_workflow("帮我写一首关于夏天的诗", use_llm=False)
        self.assertEqual(state.query.primary_intent, WorkflowIntent.OUT_OF_SCOPE)
        self.assertEqual(state.phase, WorkflowPhase.FAILED)
        self.assertFalse(state.capability_id)
        self.assertFalse(state.allowed_tools)

    def test_non_aiops_explanation_is_out_of_scope(self) -> None:
        result = deterministic_understanding("量子纠缠是什么，为什么会发生？")
        self.assertEqual(result.primary_intent, WorkflowIntent.OUT_OF_SCOPE)

    def test_negated_write_does_not_raise_risk(self) -> None:
        result = deterministic_understanding("不要重启，只查看本机后台进程")
        self.assertEqual(result.primary_intent, WorkflowIntent.STATUS_QUERY)
        self.assertEqual(result.risk_level.value, "read_only")
        self.assertFalse(result.requires_confirmation)

    def test_explanatory_oom_with_live_task_routes_status(self) -> None:
        result = deterministic_understanding("先解释 OOM 是什么，再查看本机内存占用")
        self.assertEqual(result.primary_intent, WorkflowIntent.STATUS_QUERY)

    async def test_invalid_ip_scope_requires_clarification(self) -> None:
        state = await prepare_workflow(
            "查看 999.999.999.999 当前 CPU 状态",
            use_llm=False,
        )
        self.assertEqual(state.scope.kind, ScopeKind.NONE)
        self.assertEqual(state.phase, WorkflowPhase.CLARIFYING)

    async def test_prepare_local_query_resolves_scope(self) -> None:
        state = await prepare_workflow("查看本机后台进程", use_llm=False)
        self.assertEqual(state.phase, WorkflowPhase.READY)
        self.assertEqual(state.scope.kind, ScopeKind.LOCAL_HOST)
        self.assertTrue(state.scope.validated)

    async def test_prepare_remote_unknown_never_falls_back_local(self) -> None:
        state = await prepare_workflow("生产 Redis OOM，帮我排查", use_llm=False)
        self.assertEqual(state.phase, WorkflowPhase.CLARIFYING)
        self.assertEqual(state.scope.kind, ScopeKind.NONE)
        self.assertFalse(state.scope.validated)
        self.assertEqual(state.confirmation_status, ConfirmationStatus.REQUIRED)

    async def test_clarification_resolves_local_scope_without_overwriting_raw(self) -> None:
        state = await prepare_workflow("帮我检查后台进程", use_llm=False)
        updated = await apply_clarification(state, "检查本机", use_llm=False)
        self.assertEqual(updated.query.raw_query, "帮我检查后台进程")
        self.assertEqual(updated.scope.kind, ScopeKind.LOCAL_HOST)
        self.assertEqual(updated.phase, WorkflowPhase.READY)
        self.assertEqual(len(updated.clarification_history), 1)

    async def test_clarification_budget_is_enforced(self) -> None:
        state = await prepare_workflow("检查生产服务状态", use_llm=False)
        state.budgets.max_clarification_rounds = 0
        with self.assertRaises(ClarificationLimitReached):
            await apply_clarification(state, "生产环境", use_llm=False)


class ContractAndPolicyTests(unittest.TestCase):
    def test_observed_evidence_requires_valid_scope(self) -> None:
        with self.assertRaises(ValidationError):
            EvidenceItem(
                source="metric",
                type="snapshot",
                status=EvidenceStatus.OBSERVED,
                summary="CPU 90%",
            )

    def test_reference_evidence_can_be_scope_free(self) -> None:
        item = EvidenceItem(
            source="runbook",
            type="knowledge",
            status=EvidenceStatus.REFERENCE,
            summary="通用 CPU 排查手册",
        )
        self.assertEqual(item.status, EvidenceStatus.REFERENCE)

    def test_failure_retry_then_unavailable(self) -> None:
        retry = decide_failure_action(
            error_type="TimeoutError", attempt=1, max_retries=2
        )
        exhausted = decide_failure_action(
            error_type="TimeoutError", attempt=3, max_retries=2
        )
        self.assertEqual(retry.action, FailureAction.RETRY)
        self.assertEqual(exhausted.action, FailureAction.MARK_UNAVAILABLE)

    def test_illegal_phase_transition_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_phase_transition(WorkflowPhase.COMPLETED, WorkflowPhase.EXECUTING)

    def test_live_tool_policy_blocks_unconfirmed_request(self) -> None:
        understanding = deterministic_understanding("停止本机进程")
        scope = TargetScope(
            kind=ScopeKind.LOCAL_HOST,
            environment="local",
            resource_id="localhost",
            validated=True,
        )

        async def make_state():
            return await prepare_workflow(understanding.raw_query, use_llm=False)

        state = __import__("asyncio").run(make_state())
        state.scope = scope
        allowed, reason = can_execute_live_tools(state)
        self.assertFalse(allowed)
        self.assertIn("确认", reason)

    def test_tool_call_requires_valid_scope(self) -> None:
        with self.assertRaises(ValidationError):
            ToolCallEnvelope(
                run_id="run-1",
                tool_name="list_processes",
                scope=TargetScope(),
                permission_decision="allow",
            )

    def test_write_tool_requires_idempotency_key(self) -> None:
        scope = TargetScope(
            kind=ScopeKind.LOCAL_HOST,
            environment="local",
            resource_id="localhost",
            validated=True,
        )
        with self.assertRaises(ValidationError):
            ToolCallEnvelope(
                run_id="run-1",
                tool_name="restart_service",
                scope=scope,
                risk_level="high",
                permission_decision="ask",
            )

    def test_memory_reads_verified_knowledge_only(self) -> None:
        decisions = decide_memory_reads(
            intent=WorkflowIntent.FAULT_DIAGNOSIS,
            session_id="s1",
            incident_id="i1",
            service="redis",
        )
        tiers = {decision.tier for decision in decisions}
        self.assertEqual(
            tiers,
            {MemoryTier.SESSION, MemoryTier.INCIDENT, MemoryTier.VERIFIED_KNOWLEDGE},
        )
        verified = next(
            decision for decision in decisions if decision.tier == MemoryTier.VERIFIED_KNOWLEDGE
        )
        self.assertEqual(verified.filters["status"], "verified")

    def test_memory_promotion_requires_full_closed_loop(self) -> None:
        incomplete = decide_memory_writes(
            MemoryWriteContext(intent=WorkflowIntent.FAULT_DIAGNOSIS, workflow_completed=True)
        )
        complete = decide_memory_writes(
            MemoryWriteContext(
                intent=WorkflowIntent.FAULT_DIAGNOSIS,
                incident_id="i1",
                workflow_completed=True,
                human_root_cause_confirmed=True,
                remediation_verified=True,
                incident_closed=True,
                redaction_passed=True,
            )
        )
        self.assertEqual(incomplete[-1].action, MemoryAction.SKIP)
        self.assertEqual(complete[-1].action, MemoryAction.PROMOTE)


if __name__ == "__main__":
    unittest.main()
