from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.api.v1.workflows import (
    CapabilityExecuteRequest,
    StoredWorkflowRequest,
    WorkflowClarifyRequest,
    WorkflowPrepareRequest,
    _load_expected,
    clarify,
    execute_capability_stream,
    prepare,
)
from app.workflows.models import WorkflowPhase
from app.workflows.orchestrator import prepare_workflow


class WorkflowApiPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_returns_persisted_revision(self) -> None:
        async def persist(state, *, actor):
            self.assertEqual(actor, "api-user")
            state.revision = 1
            return state

        with patch(
            "app.api.v1.workflows.workflow_repository.create",
            new=AsyncMock(side_effect=persist),
        ):
            response = await prepare(
                WorkflowPrepareRequest(
                    query="查看本机后台进程",
                    use_llm=False,
                    actor="api-user",
                )
            )

        self.assertEqual(response.data.revision, 1)
        self.assertTrue(response.data.run_id)

    async def test_clarify_loads_server_state_and_saves_next_revision(self) -> None:
        state = await prepare_workflow("检查生产服务状态", use_llm=False)
        state.revision = 3

        async def save(updated, *, expected_revision, event_type, actor):
            self.assertEqual(expected_revision, 3)
            self.assertEqual(event_type, "clarification_applied")
            self.assertEqual(actor, "api-user")
            updated.revision = 4
            return updated

        with (
            patch(
                "app.api.v1.workflows.workflow_repository.get",
                new=AsyncMock(return_value=state),
            ),
            patch(
                "app.api.v1.workflows.workflow_repository.save",
                new=AsyncMock(side_effect=save),
            ),
        ):
            response = await clarify(
                WorkflowClarifyRequest(
                    run_id=state.run_id,
                    expected_revision=3,
                    answer="检查本机",
                    use_llm=False,
                    actor="api-user",
                )
            )

        self.assertEqual(response.data.revision, 4)
        self.assertEqual(response.data.scope.kind.value, "local_host")

    async def test_stale_api_revision_returns_conflict(self) -> None:
        state = await prepare_workflow("查看本机后台进程", use_llm=False)
        state.revision = 5
        with (
            patch(
                "app.api.v1.workflows.workflow_repository.get",
                new=AsyncMock(return_value=state),
            ),
            self.assertRaises(HTTPException) as raised,
        ):
            await _load_expected(
                StoredWorkflowRequest(
                    run_id=state.run_id,
                    expected_revision=4,
                )
            )
        self.assertEqual(raised.exception.status_code, 409)

    def test_client_cannot_submit_full_state_to_clarify(self) -> None:
        with self.assertRaises(ValidationError):
            WorkflowClarifyRequest.model_validate(
                {
                    "state": {"run_id": "forged"},
                    "answer": "检查本机",
                }
            )

    async def test_stream_claims_execution_before_response_is_returned(self) -> None:
        state = await prepare_workflow("查看本机后台进程", use_llm=False)
        state.revision = 2
        with patch(
            "app.api.v1.workflows._claim_expected",
            new=AsyncMock(return_value=state),
        ) as claim:
            response = await execute_capability_stream(
                CapabilityExecuteRequest(
                    run_id=state.run_id,
                    expected_revision=1,
                    actor="agent-test",
                )
            )
        claim.assert_awaited_once()
        self.assertIsNotNone(response.body_iterator)

    async def test_stream_body_uses_claimed_state_and_persists_result(self) -> None:
        state = await prepare_workflow("查看本机后台进程", use_llm=False)
        state.revision = 2

        async def capability_stream(current, **kwargs: Any):
            self.assertIs(current, state)
            current.phase = WorkflowPhase.COMPLETED
            yield {"type": "workflow_complete", "data": {}}

        async def save(current, *, expected_revision, event_type, actor):
            self.assertIs(current, state)
            self.assertEqual(expected_revision, 2)
            current.revision = 3
            return current

        with (
            patch(
                "app.api.v1.workflows._claim_expected",
                new=AsyncMock(return_value=state),
            ),
            patch(
                "app.api.v1.workflows.stream_capability_workflow",
                side_effect=capability_stream,
            ),
            patch(
                "app.api.v1.workflows.workflow_repository.save",
                new=AsyncMock(side_effect=save),
            ),
        ):
            response = await execute_capability_stream(
                CapabilityExecuteRequest(
                    run_id=state.run_id,
                    expected_revision=1,
                    actor="agent-test",
                )
            )
            chunks = [chunk async for chunk in response.body_iterator]

        self.assertEqual(len(chunks), 2)
        persisted = chunks[-1]
        self.assertIn("workflow_persisted", persisted["data"])
        self.assertIn('"revision": 3', persisted["data"])


if __name__ == "__main__":
    unittest.main()
