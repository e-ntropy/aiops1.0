from __future__ import annotations

import unittest
from unittest.mock import patch

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
    WorkflowPhase,
)
from app.workflows.orchestrator import prepare_workflow
from app.workflows.repository import (
    WorkflowConflictError,
    WorkflowLeaseError,
    WorkflowRepository,
)
from tests.test_system_inspection_workflow import FakeCollector, _snapshot


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self) -> None:
        self.executions: list[tuple[str, tuple]] = []
        self.fetches: list[tuple[str, tuple]] = []
        self.current_revision: int | None = None
        self.current_lease_owner: str | None = None
        self.force_conflict = False

    def transaction(self):
        return _AsyncContext(self)

    async def execute(self, sql: str, *args):
        self.executions.append((sql, args))
        return "OK"

    async def fetch(self, sql: str, *args):
        self.fetches.append((sql, args))
        return []

    async def fetchrow(self, sql: str, *args):
        self.fetches.append((sql, args))
        if "UPDATE workflow_runs" in sql:
            if self.force_conflict:
                return None
            return {"revision": int(args[1]) + 1}
        if "SELECT revision, lease_owner" in sql and self.current_revision is not None:
            return {
                "revision": self.current_revision,
                "lease_owner": self.current_lease_owner,
                "lease_expires_at": "future",
            }
        return None

    async def fetchval(self, sql: str, *args):
        self.fetches.append((sql, args))
        if "INSERT INTO memory_records" in sql:
            return args[0]
        if "SELECT revision FROM workflow_runs" in sql:
            return self.current_revision
        return None


class _FakePool:
    def __init__(self, conn: _FakeConnection) -> None:
        self.conn = conn

    def acquire(self):
        return _AsyncContext(self.conn)


class WorkflowRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_persists_manual_incident_state_event_and_session_memory(self) -> None:
        state = await prepare_workflow("本机内存异常，排查根因", use_llm=False)
        conn = _FakeConnection()
        repo = WorkflowRepository()
        with patch("app.workflows.repository.get_pool", return_value=_FakePool(conn)):
            stored = await repo.create(state, actor="tester")

        self.assertEqual(stored.revision, 1)
        self.assertTrue(stored.incident_id.startswith("inc_"))
        statements = "\n".join(sql for sql, _ in conn.executions)
        self.assertIn("INSERT INTO incident_groups", statements)
        self.assertIn("INSERT INTO incidents", statements)
        self.assertIn("INSERT INTO workflow_runs", statements)
        self.assertIn("INSERT INTO workflow_events", statements)
        self.assertIn("INSERT INTO memory_records", statements)

    async def test_save_uses_compare_and_swap_and_increments_revision(self) -> None:
        state = await prepare_workflow("查看本机后台进程", use_llm=False)
        state.revision = 3
        conn = _FakeConnection()
        repo = WorkflowRepository()

        await repo._save_with_conn(
            conn,
            state,
            expected_revision=3,
            event_type="test_saved",
            actor="tester",
            payload={},
        )

        self.assertEqual(state.revision, 4)
        update = next(item for item in conn.fetches if "UPDATE workflow_runs" in item[0])
        self.assertIn('"revision": 4', update[1][10])
        event = next(item for item in conn.executions if "workflow_events" in item[0])
        self.assertEqual(event[1][2], 4)

    async def test_stale_revision_is_rejected_without_event(self) -> None:
        state = await prepare_workflow("查看本机后台进程", use_llm=False)
        state.revision = 2
        conn = _FakeConnection()
        conn.force_conflict = True
        conn.current_revision = 5
        repo = WorkflowRepository()

        with self.assertRaises(WorkflowConflictError) as raised:
            await repo._save_with_conn(
                conn,
                state,
                expected_revision=2,
                event_type="stale_write",
                actor="tester",
                payload={},
            )

        self.assertEqual(raised.exception.actual, 5)
        self.assertEqual(state.revision, 2)
        self.assertFalse(any("workflow_events" in sql for sql, _ in conn.executions))

    async def test_execution_claim_prevents_parallel_runner(self) -> None:
        state = await prepare_workflow("查看本机后台进程", use_llm=False)
        state.revision = 1
        conn = _FakeConnection()
        repo = WorkflowRepository()
        with patch("app.workflows.repository.get_pool", return_value=_FakePool(conn)):
            claimed = await repo.claim_execution(
                state,
                expected_revision=1,
                actor="worker-a",
            )

        self.assertEqual(claimed.revision, 2)
        claim_sql = next(sql for sql, _ in conn.fetches if "lease_owner" in sql)
        self.assertIn("lease_expires_at", claim_sql)

        stale_state = await prepare_workflow("查看本机后台进程", use_llm=False)
        stale_state.run_id = state.run_id
        stale_state.revision = 2
        conn.force_conflict = True
        conn.current_revision = 2
        conn.current_lease_owner = "worker-a"
        with (
            patch("app.workflows.repository.get_pool", return_value=_FakePool(conn)),
            self.assertRaises(WorkflowLeaseError),
        ):
            await repo.claim_execution(
                stale_state,
                expected_revision=2,
                actor="worker-b",
            )

    async def test_recall_query_only_allows_verified_long_term_memory(self) -> None:
        state = await prepare_workflow("本机内存异常，排查根因", use_llm=False)
        conn = _FakeConnection()

        await WorkflowRepository()._recall_memories(conn, state)

        sql = conn.fetches[0][0]
        self.assertIn("tier = 'verified_knowledge' AND status = 'verified'", sql)
        self.assertIn("superseded_by IS NULL", sql)
        self.assertNotIn("tier = 'candidate'", sql)

    async def test_close_is_one_transactional_learning_write_set(self) -> None:
        state = await prepare_workflow("本机内存异常，排查根因", use_llm=False)
        state.revision = 7
        state.phase = WorkflowPhase.COMPLETED
        state.outcome.root_cause = "worker 内存持续增长"
        state.outcome.report_markdown = "# 诊断报告"
        state.outcome.confidence = 0.8
        state.evidence.append(
            EvidenceItem(
                run_id=state.run_id,
                incident_id="inc_test",
                source="fixture",
                type="system_health_snapshot",
                summary="内存 95%",
                content=_snapshot(memory=95).model_dump(mode="json"),
                scope=state.scope,
            )
        )
        state.incident_id = "inc_test"
        state = initialize_incident_lifecycle(state)
        state = confirm_diagnosis(
            state,
            decision=HumanDecisionStatus.CORRECTED,
            corrected_root_cause="worker 缓存未设置上限",
        )
        state = confirm_remediation_plan(
            state,
            decision=HumanDecisionStatus.CONFIRMED,
        )
        state = await verify_recovery(
            state,
            collector=FakeCollector(_snapshot(memory=60)),
        )
        result = close_incident(
            state,
            closed_by="tester",
            redacted_query="某 worker 出现内存压力",
            redaction_passed=True,
        )
        conn = _FakeConnection()
        repo = WorkflowRepository()
        with patch("app.workflows.repository.get_pool", return_value=_FakePool(conn)):
            stored = await repo.close_with_learning(
                result,
                expected_revision=7,
                actor="tester",
            )

        self.assertEqual(stored.state.revision, 8)
        statements = "\n".join(sql for sql, _ in conn.executions)
        self.assertIn("INSERT INTO human_decisions", statements)
        self.assertIn("INSERT INTO experience_records", statements)
        self.assertIn("INSERT INTO evaluation_samples", statements)
        self.assertIn("INSERT INTO entity_profiles", statements)
        self.assertIn("UPDATE incidents", statements)
        self.assertIn("UPDATE incident_groups", statements)


if __name__ == "__main__":
    unittest.main()
