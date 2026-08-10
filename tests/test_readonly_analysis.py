from __future__ import annotations

import unittest

from app.workflows.capabilities import CapabilityId
from app.workflows.orchestrator import prepare_workflow
from app.workflows.readonly_analysis import (
    AnalysisDataSufficiency,
    analyze_system_snapshot,
    execute_readonly_analysis,
)
from app.workflows.system_inspection import evaluate_snapshot
from tests.test_system_inspection_workflow import FakeCollector, _snapshot


class ReadonlyAnalysisTests(unittest.IsolatedAsyncioTestCase):
    def test_capacity_single_snapshot_never_claims_forecast(self) -> None:
        snapshot = _snapshot(cpu=70, memory=75, disk=65)
        result = analyze_system_snapshot(
            snapshot,
            evaluate_snapshot(snapshot),
            capability_id=CapabilityId.CAPACITY_PERFORMANCE,
        )
        self.assertEqual(result.data_sufficiency, AnalysisDataSufficiency.CURRENT_SNAPSHOT)
        self.assertFalse(result.forecast_available)
        self.assertIn("不能可靠预测", result.summary)
        self.assertGreaterEqual(len(result.required_next_data), 3)

    def test_recommendations_never_allow_execution(self) -> None:
        snapshot = _snapshot(memory=95, disk=92)
        result = analyze_system_snapshot(
            snapshot,
            evaluate_snapshot(snapshot),
            capability_id=CapabilityId.READONLY_OPTIMIZATION,
        )
        self.assertGreaterEqual(len(result.recommendations), 3)
        self.assertTrue(all(not item.execution_allowed for item in result.recommendations))
        risky = [item for item in result.recommendations if item.risk_level != "low"]
        self.assertTrue(all(item.requires_human_confirmation for item in risky))

    async def test_optimization_reuses_inspection_evidence(self) -> None:
        state = await prepare_workflow("只读优化本机内存占用", use_llm=False)
        self.assertEqual(state.capability_id, CapabilityId.READONLY_OPTIMIZATION)
        result = await execute_readonly_analysis(
            state,
            collector=FakeCollector(_snapshot(memory=91)),
        )
        self.assertEqual(len(result.state.evidence), 1)
        self.assertIn("未执行任何变更", result.analysis.summary)
        self.assertTrue(result.state.outcome.recommendations)

    async def test_capacity_flow_returns_headroom(self) -> None:
        state = await prepare_workflow("分析本机容量余量和性能瓶颈", use_llm=False)
        result = await execute_readonly_analysis(
            state,
            collector=FakeCollector(_snapshot(cpu=55, memory=60, disk=70)),
        )
        self.assertEqual(result.analysis.capability_id, CapabilityId.CAPACITY_PERFORMANCE)
        self.assertEqual(len(result.analysis.headroom), 4)
        memory = next(item for item in result.analysis.headroom if item.resource == "memory")
        self.assertEqual(memory.headroom_percent, 40)


if __name__ == "__main__":
    unittest.main()
