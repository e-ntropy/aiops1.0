from __future__ import annotations

import unittest

from benchmark.run_workflow_benchmark import (
    LIFECYCLE_EVAL_FILE,
    QUERY_EVAL_FILE,
    load_rows,
    score_lifecycle_row,
    score_query_row,
    summarize_lifecycle,
    summarize_query,
)


class WorkflowBenchmarkTests(unittest.IsolatedAsyncioTestCase):
    def test_versioned_datasets_are_loadable_and_filterable(self) -> None:
        query_rows = load_rows(QUERY_EVAL_FILE)
        lifecycle_rows = load_rows(LIFECYCLE_EVAL_FILE)
        selected = load_rows(QUERY_EVAL_FILE, ids="wf-status-local")
        self.assertGreaterEqual(len(query_rows), 20)
        self.assertGreaterEqual(len(lifecycle_rows), 9)
        self.assertEqual([item["id"] for item in selected], ["wf-status-local"])

    async def test_query_score_exposes_correctness_and_safety(self) -> None:
        row = load_rows(QUERY_EVAL_FILE, ids="wf-status-local")[0]
        detail = await score_query_row(row)
        self.assertTrue(detail["exact_match"])
        self.assertTrue(detail["safety_pass"])
        self.assertEqual(summarize_query([detail])["exact_match_rate"], 1.0)

    async def test_lifecycle_score_exposes_gates_and_promotion(self) -> None:
        row = load_rows(LIFECYCLE_EVAL_FILE, ids="lc-recovered-close")[0]
        detail = await score_lifecycle_row(row)
        self.assertTrue(detail["exact_match"])
        self.assertTrue(detail["memory_promoted"])
        self.assertEqual(summarize_lifecycle([detail])["closure_gate_accuracy"], 1.0)


if __name__ == "__main__":
    unittest.main()
