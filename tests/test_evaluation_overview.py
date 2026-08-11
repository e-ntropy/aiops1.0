from __future__ import annotations

import unittest
from unittest.mock import patch

from app.workflows.evaluation_overview import execute_evaluation_overview
from app.workflows.models import EvidenceStatus, WorkflowPhase
from app.workflows.orchestrator import prepare_workflow


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Connection:
    async def fetch(self, sql: str, *args):
        self.sql = sql
        return [
            {"capability_id": "adaptive_diagnosis", "sample_count": 4},
            {"capability_id": "system_inspection", "sample_count": 2},
        ]


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Context(self.conn)


class EvaluationOverviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_overview_combines_versioned_and_generated_samples(self) -> None:
        state = await prepare_workflow("查看诊断评测情况", use_llm=False)
        with (
            patch(
                "app.workflows.evaluation_overview.get_pool",
                return_value=_Pool(_Connection()),
            ),
            patch("app.workflows.evaluation_overview._count_jsonl", return_value=10),
        ):
            result = await execute_evaluation_overview(state)

        self.assertEqual(result.state.phase, WorkflowPhase.COMPLETED)
        self.assertEqual(sum(result.dataset_counts.values()), 70)
        self.assertEqual(result.generated_sample_count, 6)
        self.assertEqual(result.state.evidence[-1].status, EvidenceStatus.REFERENCE)
        self.assertIn("在线 Agent 只展示资产", result.state.outcome.report_markdown)


if __name__ == "__main__":
    unittest.main()
