from __future__ import annotations

import unittest

from benchmark.run_diagnosis_benchmark import (
    UsageTracker,
    dataset_fingerprint,
    extract_fast_root_cause,
    group_coverage,
    score_state,
)


class DiagnosisBenchmarkScoringTests(unittest.TestCase):
    def test_group_coverage_uses_or_within_each_group(self) -> None:
        matched, total, score = group_coverage(
            "Redis 达到 maxmemory 并返回 OOM",
            [["redis"], ["oom", "内存"], ["maxmemory"]],
        )
        self.assertEqual((matched, total, score), (3, 3, 1.0))

    def test_extract_fast_root_cause_prefers_section(self) -> None:
        report = "# 报告\n\n## 二、根因分析\n连接池泄漏导致 maxclients 耗尽。\n\n## 三、建议\n修复连接。"
        self.assertEqual(
            extract_fast_root_cause(report),
            "连接池泄漏导致 maxclients 耗尽。",
        )

    def test_deep_citation_must_be_valid_and_relevant(self) -> None:
        row = {
            "expected_root_cause_groups": [["redis"], ["oom"]],
            "evidence_term_groups": [["maxmemory"], ["ttl"]],
        }
        state = {
            "response": "report",
            "rca": {
                "root_cause": "Redis OOM",
                "supporting_evidence_ids": ["ev_0", "ev_99"],
            },
            "evidences": [
                {"source": "log", "type": "log_excerpt", "summary": "maxmemory exceeded"}
            ],
        }
        score = score_state(row, "deep", state)
        self.assertTrue(score["root_cause_top1_correct"])
        self.assertEqual(score["citation_validity"], 0.5)
        self.assertEqual(score["citation_correctness"], 0.25)

    def test_usage_tracker_reports_unavailable_before_callbacks(self) -> None:
        self.assertFalse(UsageTracker().payload()["available"])

    def test_dataset_fingerprint_includes_gold_labels(self) -> None:
        base = [{"id": "case-1", "expected_root_cause_groups": [["redis"]]}]
        changed = [{"id": "case-1", "expected_root_cause_groups": [["mysql"]]}]
        self.assertNotEqual(dataset_fingerprint(base), dataset_fingerprint(changed))


if __name__ == "__main__":
    unittest.main()
