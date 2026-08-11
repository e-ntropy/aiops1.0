"""Tool Envelope 与失败降级策略的纯离线 Benchmark。"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from pydantic import ValidationError

from app.workflows.fallbacks import decide_failure_action
from app.workflows.models import RiskLevel, ScopeKind, TargetScope
from app.workflows.tool_contracts import ToolCallEnvelope

ROOT = Path(__file__).resolve().parent.parent
EVAL_FILE = ROOT / "benchmark" / "tool_safety_eval.jsonl"
REPORT_DIR = ROOT / "benchmark" / "reports"
DEFAULT_BASELINE_FILE = ROOT / "benchmark" / "baselines" / "tool_safety_v1.json"


def _rows(limit: int | None, ids: str | None) -> list[dict[str, Any]]:
    selected = {item.strip() for item in (ids or "").split(",") if item.strip()}
    rows = [json.loads(line) for line in EVAL_FILE.read_text(encoding="utf-8").splitlines() if line]
    rows = [row for row in rows if not selected or row["id"] in selected]
    return rows[:limit] if limit else rows


def _fingerprint(rows: list[dict[str, Any]]) -> str:
    body = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def _score(row: dict[str, Any]) -> dict[str, Any]:
    if row["kind"] == "fallback":
        decision = decide_failure_action(
            error_type=row["error_type"],
            attempt=row["attempt"],
            max_retries=row["max_retries"],
            has_alternative=row["has_alternative"],
            required_source=row["required_source"],
        )
        checks = {
            "action": decision.action.value == row["expected_action"],
            "retry_delay": decision.retry_after_ms == row["expected_retry_after_ms"],
        }
    else:
        valid = True
        try:
            ToolCallEnvelope(
                run_id="benchmark-run",
                tool_name="benchmark_tool",
                scope=TargetScope(
                    kind=ScopeKind(row["scope_kind"]),
                    resource_id="localhost" if row["scope_kind"] != "none" else "",
                    validated=row["scope_validated"],
                    source="benchmark",
                ),
                risk_level=RiskLevel(row["risk_level"]),
                permission_decision=row["permission_decision"],
                idempotency_key=row["idempotency_key"],
                timeout_sec=row["timeout_sec"],
            )
        except ValidationError:
            valid = False
        checks = {"envelope_validity": valid == row["expected_valid"]}
    return {"id": row["id"], "kind": row["kind"], "checks": checks, "exact_match": all(checks.values())}


def run_tool_safety_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    rows = _rows(args.limit, args.ids)
    details = [_score(row) for row in rows]
    by_kind = {
        kind: mean(item["exact_match"] for item in details if item["kind"] == kind)
        for kind in {item["kind"] for item in details}
    }
    payload = {
        "mode": "tool_safety",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "offline": True,
        "dataset": {"file": EVAL_FILE.name, "rows": len(rows), "sha256": _fingerprint(rows)},
        "summary": {"rows": len(rows), "exact_match_rate": mean(item["exact_match"] for item in details), "by_kind": by_kind},
        "details": details,
    }
    if args.enforce or args.baseline:
        path = Path(args.baseline) if args.baseline else DEFAULT_BASELINE_FILE
        baseline = json.loads(path.read_text(encoding="utf-8"))
        passed = (
            payload["dataset"]["rows"] >= baseline["dataset"]["rows"]
            and payload["dataset"]["sha256"] == baseline["dataset"]["sha256"]
            and payload["summary"]["exact_match_rate"]
            >= baseline["minimum_exact_match_rate"]
        )
        payload["gate"] = {"passed": passed}
    output = Path(args.output) if args.output else REPORT_DIR / f"tool_safety_{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"tool_safety | rows={len(rows)} exact={payload['summary']['exact_match_rate']:.3f} fallback={by_kind.get('fallback', 0):.3f} envelope={by_kind.get('envelope', 0):.3f}")
    print(f"report: {output}")
    if args.enforce and not payload.get("gate", {}).get("passed", False):
        raise SystemExit("Tool safety benchmark gate failed")
    return payload
