"""统一工作流与事故生命周期的纯离线 Contract Benchmark。

不调用 LLM、Milvus、Postgres、Redis 或真实系统采集器。它评估确定性 Query/Scope/
Capability 契约，以及人工确认、恢复验证、关闭和 Memory 晋升状态机。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BENCH_DIR = ROOT / "benchmark"
REPORT_DIR = BENCH_DIR / "reports"
QUERY_EVAL_FILE = BENCH_DIR / "workflow_contract_eval.jsonl"
LIFECYCLE_EVAL_FILE = BENCH_DIR / "lifecycle_contract_eval.jsonl"


def load_rows(
    path: Path,
    *,
    limit: int | None = None,
    ids: str | None = None,
) -> list[dict[str, Any]]:
    selected = {item.strip() for item in (ids or "").split(",") if item.strip()}
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
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
        1
        for alternatives in groups
        if any(_normalize(term) in body for term in alternatives)
    )
    return matched / len(groups)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


async def score_query_row(row: dict[str, Any]) -> dict[str, Any]:
    from app.tools.meta import get_meta
    from app.workflows.background_policy import assess_background_execution
    from app.workflows.models import RiskLevel, ScopeKind, WorkflowPhase
    from app.workflows.orchestrator import prepare_workflow

    started = time.perf_counter()
    state = await prepare_workflow(str(row["query"]), use_llm=False)
    predicted = {
        "intent": state.query.primary_intent.value,
        "capability": state.capability_id,
        "phase": state.phase.value,
        "scope_kind": state.scope.kind.value,
        "confirmation": state.query.requires_confirmation,
        "risk": state.query.risk_level.value,
    }
    expected = {
        "intent": str(row["expected_intent"]),
        "capability": str(row.get("expected_capability") or ""),
        "phase": str(row["expected_phase"]),
        "scope_kind": str(row["expected_scope_kind"]),
        "confirmation": bool(row["expected_confirmation"]),
        "risk": str(row.get("expected_risk") or RiskLevel.READ_ONLY.value),
    }
    correctness = {
        key: predicted[key] == expected[key]
        for key in expected
    }
    subtask_text = "\n".join(item.objective for item in state.plan)
    subtask_coverage = _group_coverage(
        subtask_text,
        list(row.get("subtask_term_groups") or []),
    )
    requires_live_scope = state.query.primary_intent.value in {
        "status_query",
        "system_inspection",
        "fault_diagnosis",
        "optimization",
        "capacity_performance",
    }
    safety_checks = {
        "allowed_tools_read_only": all(
            get_meta(name).effective_read_only({}) for name in state.allowed_tools
        ),
        "unresolved_live_scope_not_ready": not (
            requires_live_scope
            and not state.scope.validated
            and state.phase == WorkflowPhase.READY
        ),
        "high_risk_requires_confirmation": not (
            state.query.risk_level == RiskLevel.HIGH
            and state.phase != WorkflowPhase.CLARIFYING
        ),
        "no_implicit_local_scope": not (
            state.scope.kind == ScopeKind.LOCAL_HOST
            and not any(
                term in state.query.raw_query.lower()
                for term in ("本机", "这台电脑", "我的电脑", "当前电脑", "localhost", "本地机器")
            )
        ),
    }
    if state.scope.kind in {ScopeKind.LOCAL_HOST, ScopeKind.REMOTE_HOST}:
        safety_checks["live_background_closed"] = not assess_background_execution(state).can_submit
    safety_pass = all(safety_checks.values())
    exact_match = all(correctness.values()) and subtask_coverage == 1.0 and safety_pass
    return {
        "id": row["id"],
        "scenario": row.get("scenario"),
        "query": row["query"],
        "known_difficulty": row.get("known_difficulty", ""),
        "expected": expected,
        "predicted": predicted,
        "correctness": correctness,
        "subtask_coverage": subtask_coverage,
        "safety_checks": safety_checks,
        "safety_pass": safety_pass,
        "exact_match": exact_match,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _snapshot(values: dict[str, Any] | None) -> Any:
    from app.workflows.system_inspection import DiskSnapshot, SystemHealthSnapshot

    values = values or {}
    disk = float(values.get("disk", 40))
    return SystemHealthSnapshot(
        os="BenchmarkOS",
        cpu_percent=float(values.get("cpu", 20)),
        memory_percent=float(values.get("memory", 30)),
        swap_percent=float(values.get("swap", 0)),
        memory_used_gb=3,
        memory_available_gb=7,
        memory_total_gb=10,
        disks=[
            DiskSnapshot(
                device="benchmark",
                mountpoint="/data",
                total_gb=100,
                used_gb=disk,
                free_gb=100 - disk,
                percent=disk,
            )
        ],
    )


class _SnapshotCollector:
    def __init__(self, snapshot: Any) -> None:
        self.snapshot = snapshot

    def collect(self, process_limit: int = 10) -> Any:
        return self.snapshot


class _FailingCollector:
    def __init__(self, error_type: str) -> None:
        self.error_type = error_type

    def collect(self, process_limit: int = 10) -> Any:
        if self.error_type == "TimeoutError":
            raise TimeoutError("benchmark collector timeout")
        raise ConnectionError("benchmark collector offline")


async def score_lifecycle_row(row: dict[str, Any]) -> dict[str, Any]:
    from app.workflows.incident_lifecycle import (
        close_incident,
        confirm_diagnosis,
        confirm_remediation_plan,
        initialize_incident_lifecycle,
        verify_recovery,
    )
    from app.workflows.models import (
        EvidenceItem,
        HumanDecisionStatus,
        TargetScope,
        WorkflowPhase,
    )
    from app.workflows.orchestrator import prepare_workflow

    started = time.perf_counter()
    state = await prepare_workflow("本机内存异常，排查根因", use_llm=False)
    state.phase = WorkflowPhase.COMPLETED
    state.outcome.root_cause = "worker 进程内存持续增长"
    state.outcome.report_markdown = "# 诊断报告\n\n结构化基线显示内存压力。"
    state.outcome.recommendations = ["人工评估缓存上限，不自动执行变更"]
    if row.get("baseline") is not None:
        baseline_scope = state.scope
        if row.get("baseline_scope_matches", True) is False:
            baseline_scope = TargetScope(
                kind="local_host",
                resource_id="another-host",
                validated=True,
                source="benchmark",
            )
        state.evidence.append(
            EvidenceItem(
                run_id=state.run_id,
                source="benchmark_fixture",
                type="system_health_snapshot",
                summary="脱敏的结构化诊断基线",
                content=_snapshot(row.get("baseline")).model_dump(mode="json"),
                scope=baseline_scope,
            )
        )
    state = initialize_incident_lifecycle(state)
    diagnosis_decision = HumanDecisionStatus(str(row.get("diagnosis_decision", "confirmed")))
    state = confirm_diagnosis(
        state,
        decision=diagnosis_decision,
        corrected_root_cause=str(row.get("corrected_root_cause") or ""),
        note="benchmark human decision",
    )
    if diagnosis_decision != HumanDecisionStatus.REJECTED:
        plan_decision = HumanDecisionStatus(str(row.get("plan_decision", "confirmed")))
        state = confirm_remediation_plan(
            state,
            decision=plan_decision,
            note="benchmark plan decision",
        )
        if plan_decision == HumanDecisionStatus.CONFIRMED:
            state.budgets.max_tool_retries = 1
            collector: Any
            if row.get("collector_error"):
                collector = _FailingCollector(str(row["collector_error"]))
            else:
                collector = _SnapshotCollector(_snapshot(row.get("recovery")))
            state = await verify_recovery(
                state,
                collector=collector,
                retry_sleep=lambda _delay: None,
            )

    close_allowed = False
    close_error = ""
    if row.get("attempt_close"):
        try:
            result = close_incident(
                state,
                closed_by="benchmark-reviewer",
                redacted_query="某 worker 出现内存压力",
                redaction_passed=bool(row.get("redaction_passed")),
            )
            state = result.state
            close_allowed = True
        except (PermissionError, ValueError) as exc:
            close_error = f"{type(exc).__name__}: {exc}"

    promoted = any(
        item.get("tier") == "verified_knowledge" and item.get("action") == "promote"
        for item in state.memory.write_decisions
    )
    verification_id = state.lifecycle.verification.verification_evidence_id
    verification_evidence = next(
        (item for item in state.evidence if item.id == verification_id),
        None,
    )
    evidence_integrity = verification_evidence is None or (
        verification_evidence.scope == state.scope
        and bool(verification_evidence.tool_call_id)
        and verification_evidence.status.value == "observed"
    )
    expected_failures = row.get("expected_failure_records")
    checks = {
        "stage": state.lifecycle.stage.value == str(row["expected_stage"]),
        "closure_gate": close_allowed == bool(row["expected_close_allowed"]),
        "memory_promotion": promoted == bool(row["expected_memory_promoted"]),
        "evidence_scope_integrity": evidence_integrity,
        "failure_contract": (
            expected_failures is None or len(state.failures) == int(expected_failures)
        ),
    }
    return {
        "id": row["id"],
        "scenario": row.get("scenario"),
        "expected_stage": row["expected_stage"],
        "actual_stage": state.lifecycle.stage.value,
        "close_allowed": close_allowed,
        "close_error": close_error,
        "memory_promoted": promoted,
        "failure_records": len(state.failures),
        "checks": checks,
        "exact_match": all(checks.values()),
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def summarize_query(details: list[dict[str, Any]]) -> dict[str, Any]:
    keys = ("intent", "capability", "phase", "scope_kind", "confirmation", "risk")
    summary = {
        f"{key}_accuracy": _mean(
            [float(item["correctness"][key]) for item in details]
        )
        for key in keys
    }
    summary.update(
        {
            "rows": len(details),
            "mean_subtask_coverage": _mean(
                [float(item["subtask_coverage"]) for item in details]
            ),
            "safety_pass_rate": _mean(
                [float(item["safety_pass"]) for item in details]
            ),
            "exact_match_rate": _mean(
                [float(item["exact_match"]) for item in details]
            ),
            "mean_latency_ms": _mean(
                [float(item["latency_ms"]) for item in details]
            ),
        }
    )
    return summary


def summarize_lifecycle(details: list[dict[str, Any]]) -> dict[str, Any]:
    check_keys = (
        "stage",
        "closure_gate",
        "memory_promotion",
        "evidence_scope_integrity",
        "failure_contract",
    )
    summary = {
        f"{key}_accuracy": _mean(
            [float(item["checks"][key]) for item in details]
        )
        for key in check_keys
    }
    summary.update(
        {
            "rows": len(details),
            "exact_match_rate": _mean(
                [float(item["exact_match"]) for item in details]
            ),
            "mean_latency_ms": _mean(
                [float(item["latency_ms"]) for item in details]
            ),
        }
    )
    return summary


def write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


async def run_workflow_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    suite = str(args.suite)
    query_rows = (
        load_rows(QUERY_EVAL_FILE, limit=args.limit, ids=args.ids)
        if suite in {"all", "query"}
        else []
    )
    lifecycle_rows = (
        load_rows(LIFECYCLE_EVAL_FILE, limit=args.limit, ids=args.ids)
        if suite in {"all", "lifecycle"}
        else []
    )
    if not query_rows and not lifecycle_rows:
        raise SystemExit("No workflow contract evaluation rows selected.")
    started = time.perf_counter()
    query_details = [await score_query_row(row) for row in query_rows]
    lifecycle_details = [await score_lifecycle_row(row) for row in lifecycle_rows]
    payload = {
        "mode": "workflow_contract",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "offline": True,
        "suite": suite,
        "rows": len(query_details) + len(lifecycle_details),
        "datasets": {
            "query": {
                "file": QUERY_EVAL_FILE.name,
                "rows": len(query_rows),
                "sha256": dataset_fingerprint(query_rows) if query_rows else "",
            },
            "lifecycle": {
                "file": LIFECYCLE_EVAL_FILE.name,
                "rows": len(lifecycle_rows),
                "sha256": dataset_fingerprint(lifecycle_rows) if lifecycle_rows else "",
            },
        },
        "query_summary": summarize_query(query_details) if query_details else {},
        "lifecycle_summary": (
            summarize_lifecycle(lifecycle_details) if lifecycle_details else {}
        ),
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "details": {
            "query": query_details,
            "lifecycle": lifecycle_details,
        },
    }
    tag = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output = Path(args.output) if args.output else REPORT_DIR / f"workflow_contract_{tag}.json"
    if not output.is_absolute():
        output = ROOT / output
    write_report(output, payload)
    if query_details:
        summary = payload["query_summary"]
        print(
            "query | "
            f"rows={summary['rows']} intent={summary['intent_accuracy']:.3f} "
            f"capability={summary['capability_accuracy']:.3f} "
            f"scope={summary['scope_kind_accuracy']:.3f} "
            f"safety={summary['safety_pass_rate']:.3f} "
            f"exact={summary['exact_match_rate']:.3f}"
        )
    if lifecycle_details:
        summary = payload["lifecycle_summary"]
        print(
            "lifecycle | "
            f"rows={summary['rows']} stage={summary['stage_accuracy']:.3f} "
            f"closure={summary['closure_gate_accuracy']:.3f} "
            f"memory={summary['memory_promotion_accuracy']:.3f} "
            f"exact={summary['exact_match_rate']:.3f}"
        )
    print(f"report: {output}")
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline workflow contract benchmark")
    parser.add_argument("--suite", choices=("all", "query", "lifecycle"), default="all")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ids", type=str, default=None, help="Comma-separated case IDs")
    parser.add_argument("--output", type=str, default=None)
    return parser


def main() -> None:
    asyncio.run(run_workflow_benchmark(build_parser().parse_args()))


if __name__ == "__main__":
    main()
