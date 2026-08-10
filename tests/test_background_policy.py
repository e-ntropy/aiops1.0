from __future__ import annotations

import unittest

from app.workflows.background_policy import assess_background_execution
from app.workflows.orchestrator import prepare_workflow


class BackgroundPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_live_workflow_requires_target_affinity(self) -> None:
        state = await prepare_workflow("检查本机 CPU 和内存状态", use_llm=False)
        decision = assess_background_execution(state)
        self.assertFalse(decision.can_submit)
        self.assertFalse(decision.capability_eligible)
        self.assertEqual(decision.required_executor, "target_affine_agent")
        self.assertEqual(decision.redis_payload_contract, "task_reference_only")

    async def test_knowledge_is_semantically_eligible_but_adapter_is_not_ready(self) -> None:
        state = await prepare_workflow("请介绍 SRE 错误预算的基本概念", use_llm=False)
        decision = assess_background_execution(state)
        self.assertTrue(decision.capability_eligible)
        self.assertFalse(decision.adapter_ready)
        self.assertFalse(decision.can_submit)
        self.assertEqual(decision.required_executor, "capability_worker")


if __name__ == "__main__":
    unittest.main()
