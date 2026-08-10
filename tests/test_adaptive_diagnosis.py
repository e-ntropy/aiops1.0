from __future__ import annotations

import unittest
from collections.abc import AsyncIterator
from typing import Any

from app.workflows.adaptive_diagnosis import stream_adaptive_diagnosis
from app.workflows.orchestrator import prepare_workflow


class FakeDiagnosisRunner:
    def __init__(self, fast_events: list[dict[str, Any]], deep_events: list[dict[str, Any]]) -> None:
        self.fast_events = fast_events
        self.deep_events = deep_events
        self.calls: list[str] = []

    async def __call__(self, query: str, **kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        mode = str(kwargs["diagnosis_mode"])
        self.calls.append(mode)
        events = self.fast_events if mode == "fast" else self.deep_events
        for event in events:
            yield event


def _tool_event(name: str) -> dict[str, Any]:
    return {
        "type": "tool_call",
        "message": "",
        "data": {
            "name": name,
            "status": "ok",
            "read_only": True,
            "elapsed_ms": 10,
            "result_chars": 100,
        },
    }


def _report(text: str) -> dict[str, Any]:
    return {"type": "report", "message": "report", "data": {"report": text}}


class AdaptiveDiagnosisTests(unittest.IsolatedAsyncioTestCase):
    async def _state(self):
        return await prepare_workflow("本机内存异常，排查根因", use_llm=False)

    async def test_fast_completes_when_two_sources_and_report_exist(self) -> None:
        runner = FakeDiagnosisRunner(
            [_tool_event("get_local_cpu_memory"), _tool_event("list_top_processes"), _report("fast report")],
            [_report("deep report")],
        )
        events = [event async for event in stream_adaptive_diagnosis(await self._state(), runner=runner)]
        self.assertEqual(runner.calls, ["fast"])
        self.assertEqual(events[-1]["data"]["effective_mode"], "fast")
        self.assertEqual(events[-1]["data"]["state"]["phase"], "completed")

    async def test_single_source_escalates_to_deep_and_preserves_evidence(self) -> None:
        runner = FakeDiagnosisRunner(
            [_tool_event("get_local_cpu_memory"), _report("weak fast report")],
            [_report("deep report")],
        )
        events = [event async for event in stream_adaptive_diagnosis(await self._state(), runner=runner)]
        self.assertEqual(runner.calls, ["fast", "deep"])
        escalation = next(event for event in events if event["type"] == "deep_escalation")
        self.assertTrue(escalation["data"]["preserved_evidence_ids"])
        self.assertEqual(events[-1]["data"]["effective_mode"], "deep")

    async def test_fast_error_uses_deep_as_fallback(self) -> None:
        runner = FakeDiagnosisRunner(
            [{"type": "error", "message": "fast failed", "data": {}}],
            [_report("deep recovered")],
        )
        events = [event async for event in stream_adaptive_diagnosis(await self._state(), runner=runner)]
        self.assertEqual(runner.calls, ["fast", "deep"])
        self.assertEqual(events[-1]["type"], "adaptive_complete")

    async def test_deep_empty_report_fails_closed(self) -> None:
        runner = FakeDiagnosisRunner([_report("weak")], [])
        events = [event async for event in stream_adaptive_diagnosis(await self._state(), runner=runner)]
        self.assertEqual(events[-1]["type"], "adaptive_failed")
        self.assertEqual(events[-1]["data"]["state"]["phase"], "failed")


if __name__ == "__main__":
    unittest.main()
