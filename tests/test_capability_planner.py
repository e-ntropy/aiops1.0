from __future__ import annotations

import unittest

from pydantic import ValidationError

from app.tools.meta import get_meta
from app.workflows.capabilities import CapabilityId, list_capabilities, plan_capability
from app.workflows.models import WorkflowIntent
from app.workflows.orchestrator import prepare_workflow
from app.workflows.query_understanding import deterministic_understanding


class CapabilityPlannerTests(unittest.IsolatedAsyncioTestCase):
    def test_six_primary_product_capabilities_exist(self) -> None:
        ids = {item.id for item in list_capabilities()}
        self.assertTrue(
            {
                CapabilityId.KNOWLEDGE_QA,
                CapabilityId.SYSTEM_STATUS,
                CapabilityId.SYSTEM_INSPECTION,
                CapabilityId.ADAPTIVE_DIAGNOSIS,
                CapabilityId.READONLY_OPTIMIZATION,
                CapabilityId.CAPACITY_PERFORMANCE,
            }.issubset(ids)
        )

    def test_capability_tools_are_read_only(self) -> None:
        for capability in list_capabilities():
            self.assertTrue(capability.read_only)
            for tool_name in capability.allowed_tools:
                self.assertTrue(get_meta(tool_name).effective_read_only({}))

    async def test_prepare_state_contains_execution_contract(self) -> None:
        state = await prepare_workflow("一键巡检本机", use_llm=False)
        self.assertEqual(state.capability_id, CapabilityId.SYSTEM_INSPECTION)
        self.assertEqual(state.execution_strategy, "full_readonly_inspection")
        self.assertIn("host_resource_diagnosis", state.selected_skills)
        self.assertEqual(state.allowed_tools, ["get_system_health_snapshot"])
        self.assertTrue(state.memory.read_decisions)

    def test_capacity_query_has_own_intent(self) -> None:
        result = deterministic_understanding("分析本机未来容量余量和性能瓶颈")
        self.assertEqual(result.primary_intent, WorkflowIntent.CAPACITY_PERFORMANCE)
        plan = plan_capability(result)
        self.assertEqual(plan.capability.id, CapabilityId.CAPACITY_PERFORMANCE)

    def test_write_word_does_not_authorize_write_tool(self) -> None:
        result = deterministic_understanding("扩容本机内存并执行优化")
        plan = plan_capability(result)
        self.assertEqual(result.risk_level.value, "high")
        self.assertTrue(plan.capability.read_only)
        self.assertNotIn("docker_restart", plan.allowed_tools)

    async def test_tampered_state_cannot_add_write_tool(self) -> None:
        state = await prepare_workflow("一键巡检本机", use_llm=False)
        payload = state.model_dump()
        payload["allowed_tools"].append("docker_restart")
        with self.assertRaises(ValidationError):
            state.__class__.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
