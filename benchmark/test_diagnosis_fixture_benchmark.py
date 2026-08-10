from __future__ import annotations

import unittest

from benchmark.run_diagnosis_fixture_benchmark import load_rows, score_row


class DiagnosisFixtureDatasetTests(unittest.TestCase):
    def test_dataset_has_required_evaluation_matrix(self) -> None:
        rows = load_rows()
        self.assertGreaterEqual(len(rows), 16)
        self.assertEqual({row["task_class"] for row in rows}, {"normal", "boundary"})
        self.assertEqual(
            {row["polarity"] for row in rows},
            {"positive", "negative", "unknown"},
        )
        self.assertEqual({row["difficulty"] for row in rows}, {"simple", "complex"})
        self.assertIn("fast", {row["expected_effective_mode"] for row in rows})
        self.assertIn("deep", {row["expected_effective_mode"] for row in rows})
        self.assertIn("failed", {row["expected_phase"] for row in rows})


class DiagnosisFixtureScoringTests(unittest.IsolatedAsyncioTestCase):
    def _row(self, row_id: str):
        return next(row for row in load_rows() if row["id"] == row_id)

    async def test_simple_fast_positive_is_exact(self) -> None:
        result = await score_row(self._row("fx-host-disk-simple"))
        self.assertTrue(result["exact_match"])
        self.assertEqual(result["actual_mode"], "fast")

    async def test_complex_deep_positive_is_exact(self) -> None:
        result = await score_row(self._row("fx-mysql-lock-complex"))
        self.assertTrue(result["exact_match"])
        self.assertEqual(result["runner_calls"], ["fast", "deep"])

    async def test_negative_case_does_not_invent_fault(self) -> None:
        result = await score_row(self._row("fx-healthy-negative"))
        self.assertTrue(result["checks"]["fault_behavior"])
        self.assertTrue(result["exact_match"])

    async def test_source_failure_fails_closed_and_stays_isolated(self) -> None:
        result = await score_row(self._row("fx-all-sources-down-boundary"))
        self.assertEqual(result["actual_phase"], "failed")
        self.assertTrue(result["checks"]["fixture_isolation"])
        self.assertTrue(result["exact_match"])


if __name__ == "__main__":
    unittest.main()
