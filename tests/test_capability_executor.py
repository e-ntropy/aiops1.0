from __future__ import annotations

import unittest
from collections.abc import AsyncIterator
from typing import Any

from app.workflows.capability_executor import stream_capability_workflow
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

    async def test_unimplemented_review_fails_explicitly(self) -> None:
        state = await prepare_workflow("复盘上次事故", use_llm=False)
        with self.assertRaises(NotImplementedError):
            _ = [event async for event in stream_capability_workflow(state)]


if __name__ == "__main__":
    unittest.main()
