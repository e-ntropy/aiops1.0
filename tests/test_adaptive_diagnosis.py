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


def _evidence(source: str, evidence_type: str, *, trusted: bool = True) -> dict[str, Any]:
    return {
        "type": "evidence",
        "message": "fixture evidence",
        "data": {
            "source": source,
            "evidence_type": evidence_type,
            "status": "observed",
            "summary": f"{source} structured evidence",
            "content": {"fixture": True},
            "tool_call_id": f"tool-{source}" if trusted else "",
            "trusted_observation": trusted,
            "metadata": {"fixture_id": "unit-test"},
        },
    }


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

    async def test_structured_evidence_is_scoped_and_preserved(self) -> None:
        runner = FakeDiagnosisRunner(
            [
                _evidence("metrics_fixture", "metric_snapshot"),
                _evidence("logs_fixture", "log_excerpt"),
                _report("fixture fast report"),
            ],
            [],
        )
        events = [
            event
            async for event in stream_adaptive_diagnosis(
                await self._state(),
                runner=runner,
            )
        ]
        state = events[-1]["data"]["state"]
        observed = [item for item in state["evidence"] if item["status"] == "observed"]
        self.assertEqual(len(observed), 2)
        self.assertTrue(all(item["scope"]["resource_id"] == "localhost" for item in observed))
        self.assertTrue(all(item["tool_call_id"] for item in observed))

    async def test_untrusted_structured_observation_fails_closed(self) -> None:
        runner = FakeDiagnosisRunner(
            [_evidence("model_text", "metric_snapshot", trusted=False), _report("weak")],
            [],
        )
        events = [
            event
            async for event in stream_adaptive_diagnosis(
                await self._state(),
                runner=runner,
            )
        ]
        self.assertEqual(events[-1]["type"], "adaptive_failed")
        state = events[-1]["data"]["state"]
        self.assertEqual(state["evidence"][0]["status"], "error")
        self.assertIn("拒绝", state["evidence"][0]["summary"])

    async def test_malformed_structured_evidence_does_not_break_loop(self) -> None:
        malformed = _evidence("bad_fixture", "metric_snapshot")
        malformed["data"]["status"] = "invented_status"
        malformed["data"]["content"] = "not-a-dict"
        malformed["data"]["metadata"] = "not-a-dict"
        malformed["data"]["confidence"] = "not-a-number"
        runner = FakeDiagnosisRunner([malformed, _report("weak")], [])
        events = [
            event
            async for event in stream_adaptive_diagnosis(
                await self._state(),
                runner=runner,
            )
        ]
        self.assertEqual(events[-1]["type"], "adaptive_failed")
        item = events[-1]["data"]["state"]["evidence"][0]
        self.assertEqual(item["status"], "error")
        self.assertEqual(item["content"], {"raw_value_rejected": True})

    async def test_non_mapping_event_data_is_ignored_safely(self) -> None:
        runner = FakeDiagnosisRunner(
            [{"type": "evidence", "message": "bad", "data": "not-a-mapping"}],
            [],
        )
        events = [
            event
            async for event in stream_adaptive_diagnosis(
                await self._state(),
                runner=runner,
            )
        ]
        self.assertEqual(events[-1]["type"], "adaptive_failed")
        self.assertEqual(events[-1]["data"]["state"]["evidence"][0]["status"], "error")


if __name__ == "__main__":
    unittest.main()
