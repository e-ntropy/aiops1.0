from __future__ import annotations

import unittest
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, patch

from app.workflows.capability_executor import stream_capability_workflow
from app.workflows.evaluation_overview import EvaluationOverviewResult
from app.workflows.incident_review import IncidentReviewResult
from app.workflows.models import WorkflowPhase
from app.workflows.orchestrator import prepare_workflow


async def fake_rag_streamer(*args: Any, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "progress", "data": {"sources": ["redis-sop.md"]}}
    yield {"type": "token", "content": "Redis "}
    yield {"type": "token", "content": "知识回答"}


class FakeAdaptiveStreamer:
    def __init__(self) -> None:
        self.called = False

    async def __call__(self, state, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        self.called = True
        yield {"type": "adaptive_complete", "message": "done", "data": {}}


class CapabilityExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def test_knowledge_uses_rag_without_live_scope(self) -> None:
        state = await prepare_workflow("Redis RDB 和 AOF 有什么区别", use_llm=False)
        events = [
            event
            async for event in stream_capability_workflow(
                state,
                rag_streamer=fake_rag_streamer,
            )
        ]
        self.assertEqual(events[-1]["type"], "workflow_complete")
        final_state = events[-1]["data"]["state"]
        self.assertEqual(final_state["phase"], "completed")
        self.assertEqual(final_state["evidence"][0]["status"], "reference")
        self.assertIn("知识回答", final_state["outcome"]["report_markdown"])
        write_tiers = [item["tier"] for item in final_state["memory"]["write_decisions"]]
        self.assertEqual(write_tiers, ["session", "verified_knowledge"])

    async def test_adaptive_capability_delegates_without_duplication(self) -> None:
        state = await prepare_workflow("本机内存异常，排查根因", use_llm=False)
        adaptive = FakeAdaptiveStreamer()
        events = [
            event
            async for event in stream_capability_workflow(
                state,
                adaptive_streamer=adaptive,
            )
        ]
        self.assertTrue(adaptive.called)
        self.assertEqual(events[1]["data"]["event"]["type"], "adaptive_complete")

    async def test_incident_review_uses_postgres_fact_service(self) -> None:
        state = await prepare_workflow("复盘上次事故", use_llm=False)
        reviewed = state.model_copy(deep=True)
        reviewed.phase = WorkflowPhase.COMPLETED
        reviewed.outcome.summary = "已基于 Postgres 事实生成事故复盘"
        reviewed.outcome.report_markdown = "# 事故复盘"
        with patch(
            "app.workflows.capability_executor.execute_incident_review",
            new=AsyncMock(
                return_value=IncidentReviewResult(
                    state=reviewed,
                    source_run_id="run-source",
                    event_count=8,
                    decision_count=3,
                )
            ),
        ):
            events = [event async for event in stream_capability_workflow(state)]
        self.assertEqual(events[-1]["type"], "workflow_complete")
        self.assertEqual(events[-1]["data"]["result"]["event_count"], 8)

    async def test_evaluation_uses_readonly_inventory_service(self) -> None:
        state = await prepare_workflow("查看诊断评测情况", use_llm=False)
        evaluated = state.model_copy(deep=True)
        evaluated.phase = WorkflowPhase.COMPLETED
        evaluated.outcome.summary = "已汇总可复现评测资产"
        with patch(
            "app.workflows.capability_executor.execute_evaluation_overview",
            new=AsyncMock(
                return_value=EvaluationOverviewResult(
                    state=evaluated,
                    dataset_counts={"workflow_contract": 20},
                    generated_sample_count=6,
                )
            ),
        ):
            events = [event async for event in stream_capability_workflow(state)]
        self.assertEqual(events[-1]["type"], "workflow_complete")
        self.assertEqual(events[-1]["data"]["result"]["generated_sample_count"], 6)


if __name__ == "__main__":
    unittest.main()
