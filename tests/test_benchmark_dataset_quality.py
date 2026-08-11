from __future__ import annotations

import unittest
from collections import Counter

from benchmark.validate_scaled_benchmarks import read_jsonl, validate_inventory


class BenchmarkDatasetQualityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.summary = validate_inventory()

    def test_versioned_inventory_contains_990_rows(self) -> None:
        self.assertEqual(self.summary["total_rows"], 990)

    def test_scaled_inventory_contains_840_rows(self) -> None:
        self.assertEqual(self.summary["scaled_rows"], 840)

    def test_boundary_share_meets_release_floor(self) -> None:
        self.assertGreaterEqual(self.summary["boundary_ratio"], 0.35)

    def test_complex_share_meets_release_floor(self) -> None:
        self.assertGreaterEqual(self.summary["complex_ratio"], 0.40)

    def test_query_has_twelve_balanced_intent_families(self) -> None:
        rows = read_jsonl("workflow_contract_eval.jsonl")
        counts = Counter(item["family_id"] for item in rows)
        self.assertEqual(len(counts), 12)
        self.assertEqual(set(counts.values()), {20})

    def test_lifecycle_preserves_nine_incident_families(self) -> None:
        rows = read_jsonl("lifecycle_contract_eval.jsonl")
        self.assertEqual(len({item["family_id"] for item in rows}), 9)

    def test_diagnosis_covers_positive_negative_and_unknown(self) -> None:
        rows = read_jsonl("diagnosis_fixture_eval.jsonl")
        self.assertEqual({item["polarity"] for item in rows}, {"positive", "negative", "unknown"})

    def test_memory_labels_are_complete_and_disjoint(self) -> None:
        for item in read_jsonl("memory_governance_eval.jsonl"):
            expected = set(item["expected_ids"])
            forbidden = set(item["forbidden_ids"])
            records = {record["id"] for record in item["records"]}
            self.assertFalse(expected & forbidden, item["id"])
            self.assertEqual(expected | forbidden, records, item["id"])

    def test_tool_safety_balances_fallback_and_envelope_cases(self) -> None:
        rows = read_jsonl("tool_safety_eval.jsonl")
        counts = Counter(item["kind"] for item in rows)
        self.assertEqual(counts, {"fallback": 60, "envelope": 60})


if __name__ == "__main__":
    unittest.main()
