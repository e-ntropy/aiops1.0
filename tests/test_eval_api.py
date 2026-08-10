from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.api.v1 import eval as eval_api


def _workflow_report() -> dict:
    return {
        "mode": "workflow_contract",
        "rows": 2,
        "elapsed_sec": 0.2,
        "query_summary": {
            "exact_match_rate": 0.5,
            "intent_accuracy": 0.5,
            "scope_kind_accuracy": 1.0,
            "safety_pass_rate": 1.0,
        },
        "lifecycle_summary": {
            "exact_match_rate": 1.0,
            "closure_gate_accuracy": 1.0,
        },
        "gate": {"baseline": "workflow_contract_v1", "passed": True},
        "details": {
            "query": [
                {
                    "id": "failed-query",
                    "scenario": "intent_conflict",
                    "query": "OOM 是什么",
                    "exact_match": False,
                    "correctness": {"intent": False},
                    "subtask_coverage": 1.0,
                    "safety_pass": True,
                }
            ],
            "lifecycle": [
                {
                    "id": "passed-lifecycle",
                    "scenario": "happy_path",
                    "exact_match": True,
                    "checks": {"stage": True},
                }
            ],
        },
    }


def _fixture_report() -> dict:
    return {
        "mode": "diagnosis_fixture",
        "rows": 2,
        "elapsed_sec": 0.1,
        "summary": {
            "phase_accuracy": 1.0,
            "mode_accuracy": 1.0,
            "fault_behavior_accuracy": 0.5,
            "mean_evidence_type_recall": 1.0,
            "isolation_pass_rate": 1.0,
            "exact_match_rate": 0.5,
        },
        "gate": {"baseline": "diagnosis_fixture_v1", "passed": False},
        "details": [
            {
                "id": "fixture-failed",
                "category": "database",
                "task_class": "boundary",
                "polarity": "unknown",
                "difficulty": "complex",
                "exact_match": False,
                "checks": {"fault_behavior": False},
                "isolation_checks": {"fixture_bound": True},
            },
            {"id": "fixture-passed", "exact_match": True},
        ],
    }


class EvalApiWorkflowReportTests(unittest.IsolatedAsyncioTestCase):
    def test_summary_exposes_release_gate(self) -> None:
        summary = eval_api._summarize(_workflow_report())
        self.assertTrue(summary["gate_pass"])

    async def test_light_report_counts_nested_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report_dir = Path(temp)
            name = "workflow_contract_20260810-120000.json"
            (report_dir / name).write_text(
                json.dumps(_workflow_report(), ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.object(eval_api, "REPORTS_DIR", report_dir):
                payload = await eval_api.get_report(name, include_details=False)
        self.assertEqual(payload["details_count"], 2)
        self.assertNotIn("details", payload)

    async def test_low_scores_returns_only_failed_contract_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            report_dir = Path(temp)
            name = "workflow_contract_20260810-120000.json"
            (report_dir / name).write_text(
                json.dumps(_workflow_report(), ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.object(eval_api, "REPORTS_DIR", report_dir):
                payload = await eval_api.list_low_scores(
                    name,
                    threshold=0.5,
                    metric="exact_match",
                    limit=20,
                )
        self.assertEqual(payload["metric"], "exact_match")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["id"], "failed-query")

    async def test_fixture_summary_and_failed_case_are_exposed(self) -> None:
        summary = eval_api._summarize(_fixture_report())
        self.assertEqual(summary["isolation_pass_rate"], 1.0)
        self.assertFalse(summary["gate_pass"])
        with tempfile.TemporaryDirectory() as temp:
            report_dir = Path(temp)
            name = "diagnosis_fixture_20260810-120000.json"
            (report_dir / name).write_text(
                json.dumps(_fixture_report(), ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.object(eval_api, "REPORTS_DIR", report_dir):
                payload = await eval_api.list_low_scores(
                    name,
                    threshold=0.5,
                    metric="exact_match",
                    limit=20,
                )
        self.assertEqual(payload["metric"], "exact_match")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["items"][0]["id"], "fixture-failed")


if __name__ == "__main__":
    unittest.main()
