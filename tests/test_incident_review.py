from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.workflows.incident_review import execute_incident_review
from app.workflows.models import HumanDecisionStatus, LifecycleStage, WorkflowPhase
from app.workflows.orchestrator import prepare_workflow


class _Context:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _ReviewConnection:
    def __init__(self, source_state) -> None:
        self.source_state = source_state

    async def fetchrow(self, sql: str, *args):
        return {
            "run_id": self.source_state.run_id,
            "state": self.source_state.model_dump(mode="json"),
        }

    async def fetch(self, sql: str, *args):
        now = datetime.now(timezone.utc)
        if "workflow_events" in sql:
            return [
                {
                    "revision": 3,
                    "event_type": "incident_closed",
                    "from_phase": "completed",
                    "to_phase": "completed",
                    "actor": "oncall",
                    "created_at": now,
                }
            ]
        if "human_decisions" in sql:
            return [
                {
                    "decision_type": "diagnosis",
                    "status": "confirmed",
                    "actor": "oncall",
                    "note": "证据一致",
                    "created_at": now,
                }
            ]
        return [
            {
                "outcome": "success",
                "summary": "恢复并关闭",
                "root_cause": "缓存无上限",
                "status": "verified",
            }
        ]


class _ReviewPool:
    def __init__(self, conn) -> None:
        self.conn = conn

    def acquire(self):
        return _Context(self.conn)


class IncidentReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_review_is_built_from_persisted_facts(self) -> None:
        source = await prepare_workflow("本机内存异常，排查根因", use_llm=False)
        source.phase = WorkflowPhase.COMPLETED
        source.incident_id = "inc-source"
        source.lifecycle.stage = LifecycleStage.CLOSED
        source.lifecycle.diagnosis.status = HumanDecisionStatus.CONFIRMED
        source.lifecycle.diagnosis.confirmed_root_cause = "worker 缓存未设置上限"
        source.lifecycle.verification.summary = "内存从 95% 降至 60%"
        review = await prepare_workflow("复盘上次事故", use_llm=False)

        with patch(
            "app.workflows.incident_review.get_pool",
            return_value=_ReviewPool(_ReviewConnection(source)),
        ):
            result = await execute_incident_review(review)

        self.assertEqual(result.state.phase, WorkflowPhase.COMPLETED)
        self.assertEqual(result.event_count, 1)
        self.assertEqual(result.decision_count, 1)
        self.assertIn("worker 缓存未设置上限", result.state.outcome.report_markdown)
        self.assertIn("incident_closed", result.state.outcome.report_markdown)
        self.assertEqual(result.state.evidence[-1].source, "postgres_incident_facts")


if __name__ == "__main__":
    unittest.main()
