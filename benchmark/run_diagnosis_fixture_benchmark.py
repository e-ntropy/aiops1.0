"""自适应诊断的纯离线事故夹具 Benchmark。

Runner 只回放版本化 JSONL 中的结构化 Evidence，不调用 LLM、数据库、向量库、
网络、Docker 或宿主机采集器。评测同时检查诊断行为和 Evidence 隔离契约。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BENCH_DIR = ROOT / "benchmark"
REPORT_DIR = BENCH_DIR / "reports"
EVAL_FILE = BENCH_DIR / "diagnosis_fixture_eval.jsonl"
DEFAULT_BASELINE_FILE = BENCH_DIR / "baselines" / "diagnosis_fixture_v1.json"


def load_rows(*, limit: int | None = None, ids: str | None = None) -> list[dict[str, Any]]:
    selected = {item.strip() for item in (ids or "").split(",") if item.strip()}
    rows: list[dict[str, Any]] = []
    with EVAL_FILE.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if selected and str(row.get("id")) not in selected:
                continue
            rows.append(row)
            if limit and len(rows) >= limit:
                break
    return rows


def dataset_fingerprint(rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").lower().split())


def _group_coverage(text: str, groups: list[list[str]]) -> float:
    if not groups:
        return 1.0
    body = _normalize(text)
    matched = sum(
        1 for alternatives in groups if any(_normalize(term) in body for term in alternatives)
    )
    return matched / len(groups)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


class FixtureDiagnosisRunner:
    """把一个脱敏事故夹具适配为生产 DiagnosisRunner 的事件协议。"""

    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row
        self.calls: list[str] = []
        self.specialist_seed_count = 0

    async def __call__(
        self,
        _query: str,
        *,
        session_id: str,
        diagnosis_mode: Any,
        cache_reports: bool,
        initial_evidence: list[dict[str, Any]] | None = None,
        recalled_memories: list[dict[str, Any]] | None = None,
        incident_group_id: str = "",
        incident_id: str = "",
        persist_legacy_wiki: bool = False,
    ):
        mode = str(getattr(diagnosis_mode, "value", diagnosis_mode)).lower()
        self.calls.append(mode)
        if mode == "deep":
            self.specialist_seed_count = len(initial_evidence or [])
        section = dict(self.row.get(mode) or {})
        for index, fixture in enumerate(section.get("evidence") or [], 1):
            status = str(fixture.get("status") or "observed")
            observed = status == "observed"
            yield {
                "type": "evidence",
                "message": f"回放 {self.row['id']} 的脱敏 {mode} Evidence",
                "data": {
                    "source": fixture.get("source") or "fixture_source",
                    "evidence_type": fixture.get("evidence_type") or "fixture_evidence",
                    "status": status,
                    "summary": fixture.get("summary") or "脱敏事故夹具 Evidence",
                    "content": dict(fixture.get("content") or {}),
                    "tool_call_id": (
                        f"fixture:{self.row['id']}:{mode}:{index}" if observed else ""
                    ),
                    "trusted_observation": observed,
                    "confidence": float(fixture.get("confidence") or 0.9),
                    "metadata": {
                        "fixture_id": self.row["id"],
                        "dataset_only": True,
                        "category": self.row["category"],
                        "task_class": self.row["task_class"],
                        "polarity": self.row["polarity"],
                        "difficulty": self.row["difficulty"],
                    },
                },
            }
        if section.get("error"):
            yield {
                "type": "error",
                "message": str(section["error"]),
                "data": {"fixture_id": self.row["id"], "mode": mode},
            }
        if section.get("report"):
            yield {
                "type": "report",
                "message": f"{mode} fixture report ready",
                "data": {"report": str(section["report"])},
            }


def _score_fault_behavior(row: dict[str, Any], report: str, phase: str) -> bool:
    polarity = str(row["polarity"])
    if polarity == "positive":
        return _group_coverage(report, row.get("expected_root_cause_groups") or []) == 1.0
    if polarity == "negative":
        markers = ("未发现", "无持续故障", "不能证明", "当前正常", "已恢复")
        return any(marker in report for marker in markers)
    markers = ("无法确认", "证据冲突", "证据不足", "数据源不可用")
    return phase == "failed" or any(marker in report for marker in markers)


async def score_row(row: dict[str, Any]) -> dict[str, Any]:
    from app.workflows.adaptive_diagnosis import stream_adaptive_diagnosis
    from app.workflows.orchestrator import prepare_workflow

    started = time.perf_counter()
    state = await prepare_workflow(str(row["query"]), use_llm=False)
    runner = FixtureDiagnosisRunner(row)
    events = [event async for event in stream_adaptive_diagnosis(state, runner=runner)]
    terminal = next(
        event for event in reversed(events) if event["type"] in {"adaptive_complete", "adaptive_failed"}
    )
    final_state = dict((terminal.get("data") or {}).get("state") or {})
    phase = str(final_state.get("phase") or "")
    effective_mode = str((terminal.get("data") or {}).get("effective_mode") or "none")
    report = str((final_state.get("outcome") or {}).get("report_markdown") or "")
    evidence = list(final_state.get("evidence") or [])
    observed = [item for item in evidence if item.get("status") == "observed"]
    observed_types = {str(item.get("type") or "") for item in observed}
    expected_types = set(row.get("expected_evidence_types") or [])
    evidence_type_recall = (
        len(observed_types & expected_types) / len(expected_types) if expected_types else 1.0
    )
    scope = final_state.get("scope") or {}
    forbidden_sources = {
        "local_system",
        "get_local_system_overview",
        "get_system_health_snapshot",
        "get_local_cpu_memory",
        "get_local_disk_usage",
        "list_top_processes",
    }
    triage_has_seedable_evidence = any(
        str(item.get("status") or "observed") in {"observed", "reference"}
        for item in (row.get("fast") or {}).get("evidence") or []
    )
    isolation_checks = {
        "fixture_bound": all(
            (item.get("metadata") or {}).get("fixture_id") == row["id"] for item in observed
        ),
        "dataset_only": all(
            (item.get("metadata") or {}).get("dataset_only") is True for item in observed
        ),
        "tool_call_bound": all(bool(item.get("tool_call_id")) for item in observed),
        "scope_bound": all((item.get("scope") or {}) == scope for item in observed),
        "no_real_host_source": all(item.get("source") not in forbidden_sources for item in observed),
        "runner_modes": runner.calls
        == (["fast"] if row["expected_effective_mode"] == "fast" else ["fast", "deep"]),
        "specialist_receives_triage_evidence": (
            runner.specialist_seed_count > 0
            if "deep" in runner.calls and triage_has_seedable_evidence
            else runner.specialist_seed_count == 0
        ),
    }
    checks = {
        "phase": phase == str(row["expected_phase"]),
        "mode": effective_mode == str(row["expected_effective_mode"]),
        "fault_behavior": _score_fault_behavior(row, report, phase),
        "root_cause_groups": _group_coverage(
            report, row.get("expected_root_cause_groups") or []
        )
        == 1.0,
        "evidence_types": evidence_type_recall == 1.0,
        "fixture_isolation": all(isolation_checks.values()),
    }
    return {
        "id": row["id"],
        "category": row["category"],
        "task_class": row["task_class"],
        "polarity": row["polarity"],
        "difficulty": row["difficulty"],
        "expected_phase": row["expected_phase"],
        "actual_phase": phase,
        "expected_mode": row["expected_effective_mode"],
        "actual_mode": effective_mode,
        "runner_calls": runner.calls,
        "report": report,
        "root_cause_group_coverage": _group_coverage(
            report, row.get("expected_root_cause_groups") or []
        ),
        "evidence_type_recall": evidence_type_recall,
        "isolation_checks": isolation_checks,
        "checks": checks,
        "exact_match": all(checks.values()),
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def summarize(details: list[dict[str, Any]]) -> dict[str, Any]:
    check_names = ("phase", "mode", "fault_behavior", "root_cause_groups", "evidence_types")
    summary = {
        "rows": len(details),
        **{
            f"{name}_accuracy": _mean([float(item["checks"][name]) for item in details])
            for name in check_names
        },
        "mean_root_cause_group_coverage": _mean(
            [float(item["root_cause_group_coverage"]) for item in details]
        ),
        "mean_evidence_type_recall": _mean(
            [float(item["evidence_type_recall"]) for item in details]
        ),
        "isolation_pass_rate": _mean(
            [float(item["checks"]["fixture_isolation"]) for item in details]
        ),
        "exact_match_rate": _mean([float(item["exact_match"]) for item in details]),
        "mean_latency_ms": _mean([float(item["latency_ms"]) for item in details]),
    }
    grouped: dict[str, dict[str, Any]] = {}
    for field in ("task_class", "polarity", "difficulty", "category"):
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for item in details:
            buckets[str(item[field])].append(item)
        grouped[field] = {
            value: {
                "rows": len(items),
                "exact_match_rate": _mean([float(item["exact_match"]) for item in items]),
                "isolation_pass_rate": _mean(
                    [float(item["checks"]["fixture_isolation"]) for item in items]
                ),
            }
            for value, items in sorted(buckets.items())
        }
    summary["groups"] = grouped
    return summary


def _evaluate_gate(payload: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    dataset = payload["dataset"]
    checks = [
        {
            "name": "dataset.rows",
            "actual": dataset["rows"],
            "expected": baseline["dataset"]["rows"],
            "passed": dataset["rows"] >= baseline["dataset"]["rows"],
        },
        {
            "name": "dataset.sha256",
            "actual": dataset["sha256"],
            "expected": baseline["dataset"]["sha256"],
            "passed": dataset["sha256"] == baseline["dataset"]["sha256"],
        },
    ]
    for metric, minimum in baseline.get("thresholds", {}).items():
        actual = payload["summary"].get(metric)
        checks.append(
            {
                "name": f"summary.{metric}",
                "actual": actual,
                "expected": minimum,
                "passed": isinstance(actual, (int, float)) and actual >= minimum,
            }
        )
    return {
        "baseline": baseline.get("name", ""),
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "failed": [item for item in checks if not item["passed"]],
    }


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


async def run_diagnosis_fixture_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    rows = load_rows(limit=args.limit, ids=args.ids)
    if not rows:
        raise SystemExit("No diagnosis fixture rows selected.")
    started = time.perf_counter()
    details = [await score_row(row) for row in rows]
    payload = {
        "mode": "diagnosis_fixture",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "offline": True,
        "rows": len(rows),
        "dataset": {
            "file": EVAL_FILE.name,
            "rows": len(rows),
            "sha256": dataset_fingerprint(rows),
        },
        "summary": summarize(details),
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "details": details,
    }
    if args.enforce or args.baseline:
        baseline_path = Path(args.baseline) if args.baseline else DEFAULT_BASELINE_FILE
        if not baseline_path.is_absolute():
            baseline_path = ROOT / baseline_path
        if not baseline_path.is_file():
            raise SystemExit(f"Diagnosis fixture baseline not found: {baseline_path}")
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        payload["gate"] = _evaluate_gate(payload, baseline)
        payload["gate"]["baseline_file"] = str(baseline_path)
    tag = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output = Path(args.output) if args.output else REPORT_DIR / f"diagnosis_fixture_{tag}.json"
    if not output.is_absolute():
        output = ROOT / output
    _write_report(output, payload)
    summary = payload["summary"]
    print(
        "diagnosis_fixture | "
        f"rows={summary['rows']} phase={summary['phase_accuracy']:.3f} "
        f"mode={summary['mode_accuracy']:.3f} fault={summary['fault_behavior_accuracy']:.3f} "
        f"evidence={summary['mean_evidence_type_recall']:.3f} "
        f"isolation={summary['isolation_pass_rate']:.3f} "
        f"exact={summary['exact_match_rate']:.3f}"
    )
    print(f"report: {output}")
    if payload.get("gate"):
        gate = payload["gate"]
        print(f"gate: {'PASS' if gate['passed'] else 'FAIL'} | failed={len(gate['failed'])}")
        if args.enforce and not gate["passed"]:
            failed = ", ".join(item["name"] for item in gate["failed"])
            raise SystemExit(f"Diagnosis fixture benchmark gate failed: {failed}")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline diagnosis fixture benchmark")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ids", type=str, default=None)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--baseline", type=str, default=None)
    parser.add_argument("--enforce", action="store_true")
    return parser


def main() -> None:
    asyncio.run(run_diagnosis_fixture_benchmark(build_parser().parse_args()))


if __name__ == "__main__":
    main()
