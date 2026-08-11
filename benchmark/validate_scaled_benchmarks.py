"""Validate the versioned benchmark inventory and dataset governance invariants."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_ROOT = ROOT / "benchmark"
EXPECTED_COUNTS = {
    "workflow_contract_eval.jsonl": 240,
    "lifecycle_contract_eval.jsonl": 120,
    "diagnosis_fixture_eval.jsonl": 120,
    "memory_governance_eval.jsonl": 240,
    "tool_safety_eval.jsonl": 120,
    "skill_router_eval.jsonl": 40,
    "retrieval_rk_50.jsonl": 50,
    "ragas_qa_50.jsonl": 50,
    "diagnosis_e2e_10.jsonl": 10,
}
SCALED_FILES = tuple(list(EXPECTED_COUNTS)[:5])


def read_jsonl(filename: str) -> list[dict[str, Any]]:
    path = BENCHMARK_ROOT / filename
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def validate_inventory() -> dict[str, Any]:
    datasets = {name: read_jsonl(name) for name in EXPECTED_COUNTS}
    errors: list[str] = []
    for name, expected in EXPECTED_COUNTS.items():
        rows = datasets[name]
        if len(rows) != expected:
            errors.append(f"{name}: expected {expected}, got {len(rows)}")
        identifiers = [str(item.get("id") or item.get("query") or "") for item in rows]
        if not all(identifiers):
            errors.append(f"{name}: missing stable identifier")
        if len(identifiers) != len(set(identifiers)):
            errors.append(f"{name}: duplicate identifier")

    scaled = [item for name in SCALED_FILES for item in datasets[name]]
    boundary_rows = [item for item in scaled if item.get("task_class") == "boundary"]
    complex_rows = [item for item in scaled if item.get("difficulty") == "complex"]
    boundary_ratio = len(boundary_rows) / len(scaled)
    complex_ratio = len(complex_rows) / len(scaled)
    if boundary_ratio < 0.35:
        errors.append(f"scaled boundary ratio below 35%: {boundary_ratio:.4f}")
    if complex_ratio < 0.40:
        errors.append(f"scaled complex ratio below 40%: {complex_ratio:.4f}")
    if any(not item.get("family_id") for item in scaled):
        errors.append("scaled datasets require family_id for leakage-safe grouping")

    query_families = Counter(item["family_id"] for item in datasets[SCALED_FILES[0]])
    if len(query_families) != 12 or set(query_families.values()) != {20}:
        errors.append(f"query family distribution changed: {dict(query_families)}")
    memory_rows = datasets["memory_governance_eval.jsonl"]
    for item in memory_rows:
        expected_ids = set(item["expected_ids"])
        forbidden_ids = set(item["forbidden_ids"])
        record_ids = {record["id"] for record in item["records"]}
        if expected_ids & forbidden_ids:
            errors.append(f"{item['id']}: expected/forbidden overlap")
        if expected_ids | forbidden_ids != record_ids:
            errors.append(f"{item['id']}: memory labels do not cover every record")

    summary = {
        "dataset_counts": {name: len(rows) for name, rows in datasets.items()},
        "total_rows": sum(len(rows) for rows in datasets.values()),
        "scaled_rows": len(scaled),
        "boundary_rows": len(boundary_rows),
        "boundary_ratio": boundary_ratio,
        "complex_rows": len(complex_rows),
        "complex_ratio": complex_ratio,
        "errors": errors,
    }
    if errors:
        raise ValueError("benchmark inventory invalid:\n- " + "\n- ".join(errors))
    return summary


def main() -> None:
    print(json.dumps(validate_inventory(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
