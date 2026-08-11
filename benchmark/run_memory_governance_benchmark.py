"""Memory 治理的三策略配对消融 Benchmark。"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from app.workflows.memory_policy import MemoryWriteContext, decide_memory_writes
from app.workflows.memory_retrieval import (
    select_flat_memories,
    select_governed_memories,
)
from app.workflows.models import WorkflowIntent

ROOT = Path(__file__).resolve().parent.parent
EVAL_FILE = ROOT / "benchmark" / "memory_governance_eval.jsonl"
REPORT_DIR = ROOT / "benchmark" / "reports"
DEFAULT_BASELINE_FILE = ROOT / "benchmark" / "baselines" / "memory_governance_v1.json"


def _rows(limit: int | None = None, ids: str | None = None) -> list[dict[str, Any]]:
    selected = {item.strip() for item in (ids or "").split(",") if item.strip()}
    rows = [json.loads(line) for line in EVAL_FILE.read_text(encoding="utf-8").splitlines() if line]
    rows = [row for row in rows if not selected or row["id"] in selected]
    return rows[:limit] if limit else rows


def _fingerprint(rows: list[dict[str, Any]]) -> str:
    body = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def _wilson(successes: int, total: int) -> list[float]:
    if total <= 0:
        return [0.0, 0.0]
    z = 1.959963984540054
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def _paired_bootstrap(before: list[float], after: list[float]) -> dict[str, Any]:
    rng = random.Random(20260811)
    deltas = [right - left for left, right in zip(before, after, strict=True)]
    samples = []
    for _ in range(5000):
        samples.append(mean(rng.choice(deltas) for _ in deltas))
    samples.sort()
    return {
        "delta": mean(deltas),
        "ci95": [samples[124], samples[4874]],
        "wins": sum(delta > 0 for delta in deltas),
        "ties": sum(delta == 0 for delta in deltas),
        "losses": sum(delta < 0 for delta in deltas),
    }


def _promotion(row: dict[str, Any], strategy: str) -> bool:
    context = row["promotion_context"]
    if strategy == "no_memory":
        return False
    if strategy == "flat_memory":
        return bool(context["workflow_completed"])
    decisions = decide_memory_writes(
        MemoryWriteContext(
            intent=WorkflowIntent(context["intent"]),
            incident_id=context["incident_id"],
            workflow_completed=context["workflow_completed"],
            human_root_cause_confirmed=context["human_root_cause_confirmed"],
            remediation_verified=context["remediation_verified"],
            incident_closed=context["incident_closed"],
            redaction_passed=context["redaction_passed"],
        )
    )
    return any(
        item.tier.value == "verified_knowledge" and item.action.value == "promote"
        for item in decisions
    )


def _score(row: dict[str, Any], strategy: str) -> dict[str, Any]:
    context = row["context"]
    records = row["records"]
    if strategy == "no_memory":
        selected_records: list[dict[str, Any]] = []
    elif strategy == "flat_memory":
        selected_records = select_flat_memories(records, limit=int(context["limit"]))
    else:
        selected_records = select_governed_memories(
            records,
            session_id=context["session_id"],
            incident_id=context["incident_id"],
            service=context["service"],
            scope_key=context["scope_key"],
            limit=int(context["limit"]),
            now=datetime.fromisoformat(context["now"]),
        )
    selected = {item["id"] for item in selected_records}
    expected = set(row["expected_ids"])
    forbidden = set(row["forbidden_ids"])
    true_positive = len(selected & expected)
    precision = true_positive / len(selected) if selected else float(not expected)
    recall = true_positive / len(expected) if expected else float(not selected)
    record_correct = sum((item["id"] in selected) == (item["id"] in expected) for item in records)
    promotion = _promotion(row, strategy)
    return {
        "id": row["id"],
        "scenario": row["scenario"],
        "selected_ids": sorted(selected),
        "precision": precision,
        "recall": recall,
        "exact": selected == expected,
        "record_decision_accuracy": record_correct / len(records),
        "contaminated": bool(selected & forbidden),
        "cross_session_leak": any(
            item["id"] in selected
            and item["tier"] == "session"
            and item.get("session_id") != context["session_id"]
            for item in records
        ),
        "candidate_contamination": any(
            item["id"] in selected and item["tier"] == "candidate" for item in records
        ),
        "stale_recall": any(
            item["id"] in selected and (item.get("expires_at") or item.get("superseded_by"))
            for item in records
        ),
        "promotion_correct": promotion == bool(row["expected_promote"]),
    }


def _summary(details: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(details)
    proportions = {
        "exact_match_rate": sum(item["exact"] for item in details),
        "contamination_rate": sum(item["contaminated"] for item in details),
        "cross_session_leakage_rate": sum(item["cross_session_leak"] for item in details),
        "candidate_contamination_rate": sum(item["candidate_contamination"] for item in details),
        "stale_recall_rate": sum(bool(item["stale_recall"]) for item in details),
        "promotion_accuracy": sum(item["promotion_correct"] for item in details),
    }
    return {
        "rows": total,
        "mean_precision": mean(item["precision"] for item in details),
        "mean_recall": mean(item["recall"] for item in details),
        "record_decision_accuracy": mean(item["record_decision_accuracy"] for item in details),
        **{name: count / total for name, count in proportions.items()},
        "ci95": {name: _wilson(count, total) for name, count in proportions.items()},
    }


def _gate(payload: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    checks = [
        {"name": "dataset.rows", "passed": payload["dataset"]["rows"] >= baseline["dataset"]["rows"]},
        {"name": "dataset.sha256", "passed": payload["dataset"]["sha256"] == baseline["dataset"]["sha256"]},
    ]
    summary = payload["summaries"]["governed_memory"]
    for metric, minimum in baseline.get("minimums", {}).items():
        checks.append({"name": metric, "passed": float(summary[metric]) >= float(minimum)})
    for metric, maximum in baseline.get("maximums", {}).items():
        checks.append({"name": metric, "passed": float(summary[metric]) <= float(maximum)})
    return {"passed": all(item["passed"] for item in checks), "checks": checks, "failed": [item for item in checks if not item["passed"]]}


def run_memory_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    rows = _rows(args.limit, args.ids)
    if not rows:
        raise SystemExit("No memory governance rows selected")
    started = time.perf_counter()
    strategies = ("no_memory", "flat_memory", "governed_memory")
    details = {name: [_score(row, name) for row in rows] for name in strategies}
    summaries = {name: _summary(items) for name, items in details.items()}
    before = [item["record_decision_accuracy"] for item in details["flat_memory"]]
    after = [item["record_decision_accuracy"] for item in details["governed_memory"]]
    payload = {
        "mode": "memory_governance_ablation",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "offline": True,
        "dataset": {"file": EVAL_FILE.name, "rows": len(rows), "sha256": _fingerprint(rows)},
        "summaries": summaries,
        "comparisons": {"flat_to_governed_record_accuracy": _paired_bootstrap(before, after)},
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "details": details,
    }
    if args.enforce or args.baseline:
        path = Path(args.baseline) if args.baseline else DEFAULT_BASELINE_FILE
        baseline = json.loads(path.read_text(encoding="utf-8"))
        payload["gate"] = _gate(payload, baseline)
    output = Path(args.output) if args.output else REPORT_DIR / f"memory_governance_{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in strategies:
        item = summaries[name]
        print(f"{name} | rows={item['rows']} decision={item['record_decision_accuracy']:.3f} precision={item['mean_precision']:.3f} recall={item['mean_recall']:.3f} contamination={item['contamination_rate']:.3f} leak={item['cross_session_leakage_rate']:.3f}")
    comparison = payload["comparisons"]["flat_to_governed_record_accuracy"]
    print(f"flat_to_governed | delta={comparison['delta']:.3f} ci95={comparison['ci95']} wins={comparison['wins']} ties={comparison['ties']} losses={comparison['losses']}")
    print(f"report: {output}")
    if args.enforce and not payload.get("gate", {}).get("passed", False):
        raise SystemExit("Memory governance benchmark gate failed")
    return payload
