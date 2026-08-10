"""Skill Router 路由准确率评测.

用法:
  python benchmark/run_skill_router_benchmark.py
  python benchmark/run_skill_router_benchmark.py --limit 10
  python benchmark/run_skill_router_benchmark.py --scenario "边界case"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.runtime.transitions import ROUTER_LLM_FAILED, ROUTER_OUT_OF_SCOPE

BENCH_DIR = ROOT / "benchmark"
REPORT_DIR = BENCH_DIR / "reports"
EVAL_FILE = BENCH_DIR / "skill_router_eval.jsonl"
OUT_OF_SCOPE = "OUT_OF_SCOPE"
ERROR_OUTCOME = "ERROR"


def transition_reasons(result: dict[str, Any]) -> set[str]:
    """提取 Router 的结构化 transition reason，忽略损坏的观测项。"""
    reasons: set[str] = set()
    for item in result.get("transition_history") or []:
        if isinstance(item, dict) and item.get("reason"):
            reasons.add(str(item["reason"]))
    return reasons


def classify_router_outcome(result: dict[str, Any]) -> str:
    """把 Router state 归一为 Skill name、OUT_OF_SCOPE 或 ERROR。

    OOS 必须有明确的结构化判定信号。不能再用 ``bool(response)``，因为任何错误说明、
    提前结束报告或未来新增的直接响应都会让该条件变成假阳性。

    Router LLM 失败时没有 ROUTER_OUT_OF_SCOPE transition；此时仅接受规则兜底同时满足：
    - 记录了 ROUTER_LLM_FAILED；
    - 原因明确包含“非 OnCall”；
    - plan 为空且确实生成了拒绝诊断响应。
    """
    if result.get("__benchmark_error__"):
        return ERROR_OUTCOME

    reasons = transition_reasons(result)
    if ROUTER_OUT_OF_SCOPE in reasons:
        return OUT_OF_SCOPE

    fallback_oos = (
        ROUTER_LLM_FAILED in reasons
        and "非 OnCall" in str(result.get("skill_reason") or "")
        and not (result.get("plan") or [])
        and bool(str(result.get("response") or "").strip())
    )
    if fallback_oos:
        return OUT_OF_SCOPE
    return str(result.get("selected_skill") or "")


def score_router_result(expected: str, result: dict[str, Any]) -> tuple[bool, str]:
    """返回 (是否正确, 归一化预测结果)。"""
    predicted = classify_router_outcome(result)
    return predicted == expected, predicted


def load_eval_data(
    limit: int | None = None,
    scenario: str | None = None,
    difficulty: str | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with EVAL_FILE.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if scenario and row.get("scenario") != scenario:
                continue
            if difficulty and row.get("difficulty") != difficulty:
                continue
            rows.append(row)
            if limit and len(rows) >= limit:
                break
    return rows


@dataclass
class RouterReport:
    total: int = 0
    correct: int = 0
    out_of_scope_correct: int = 0
    out_of_scope_total: int = 0
    fallback_count: int = 0
    llm_failed_count: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)
    per_scenario: dict[str, dict[str, int]] = field(default_factory=dict)


async def run_router_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    from app.agents.skill_router import GENERIC_SKILL_NAME, skill_router_node
    from app.skills.registry import get_skill_registry

    registry = get_skill_registry()
    available = registry.names()
    print(f"Available skills: {available}")
    print(f"Generic fallback: {GENERIC_SKILL_NAME}")
    print("-" * 72)

    rows = load_eval_data(limit=args.limit, scenario=args.scenario, difficulty=args.difficulty)
    if not rows:
        raise SystemExit("No evaluation rows loaded.")

    report = RouterReport()
    t0 = time.perf_counter()

    for i, row in enumerate(rows, 1):
        user_input = str(row["input"])
        expected = str(row["expected_skill"])
        scenario = str(row.get("scenario", ""))
        difficulty = str(row.get("difficulty", ""))

        benchmark_error = ""
        try:
            result = await skill_router_node({"input": user_input})
        except Exception as exc:  # noqa: BLE001 - 每条样本必须隔离失败并计入分母
            print(f"[{i:02d}/{len(rows):02d}] ERROR: {type(exc).__name__}: {exc}")
            benchmark_error = f"{type(exc).__name__}: {exc}"
            result = {"__benchmark_error__": benchmark_error}

        selected_skill = str(result.get("selected_skill") or "")
        response = str(result.get("response") or "")
        reason = str(result.get("skill_reason") or "")
        reasons = sorted(transition_reasons(result))
        is_correct, got = score_router_result(expected, result)

        if expected == "OUT_OF_SCOPE":
            report.out_of_scope_total += 1
            if is_correct:
                report.out_of_scope_correct += 1

        # Check if fallback was used
        is_fallback = "fallback" in reason.lower() or "回退" in reason or "兜底" in reason
        is_llm_fail = ROUTER_LLM_FAILED in reasons or bool(benchmark_error)

        report.total += 1
        if is_correct:
            report.correct += 1
        if is_fallback:
            report.fallback_count += 1
        if is_llm_fail:
            report.llm_failed_count += 1

        # Per-scenario tracking
        if scenario not in report.per_scenario:
            report.per_scenario[scenario] = {"total": 0, "correct": 0}
        report.per_scenario[scenario]["total"] += 1
        if is_correct:
            report.per_scenario[scenario]["correct"] += 1

        status = "OK" if is_correct else "MISS"
        print(
            f"[{i:02d}/{len(rows):02d}] {status:<4} "
            f"accuracy={report.correct/report.total:.3f} "
            f"| {scenario} [{difficulty}] | "
            f"expected={expected:<25} got={got:<25} "
            f"| {user_input[:50]}..."
        )

        report.details.append({
            "id": row.get("id"), "input": user_input, "expected": expected,
            "got": got, "selected_skill": selected_skill,
            "correct": is_correct, "reason": reason,
            "scenario": scenario, "difficulty": difficulty,
            "is_fallback": is_fallback, "is_llm_fail": is_llm_fail,
            "transition_reasons": reasons,
            "has_direct_response": bool(response.strip()),
            "error": benchmark_error,
        })

    elapsed = time.perf_counter() - t0

    # Summary
    simple_correct = report.correct - report.out_of_scope_correct
    simple_total = report.total - report.out_of_scope_total
    print("-" * 72)
    print(f"Total: {report.total} | Correct: {report.correct} | "
          f"Accuracy: {report.correct/report.total:.1%}")
    if simple_total > 0:
        print(f"Skill matching (excl OOS): {simple_correct}/{simple_total} = {simple_correct/simple_total:.1%}")
    if report.out_of_scope_total > 0:
        print(f"Out-of-scope detection: {report.out_of_scope_correct}/{report.out_of_scope_total} = "
              f"{report.out_of_scope_correct/report.out_of_scope_total:.1%}")
    print(f"Fallback triggered: {report.fallback_count}/{report.total}")
    print(f"LLM failures: {report.llm_failed_count}")
    print(f"Elapsed: {elapsed:.1f}s")
    print()

    # Per-scenario breakdown
    print("Per-scenario breakdown:")
    for s, counts in sorted(report.per_scenario.items()):
        acc = counts["correct"] / counts["total"] if counts["total"] > 0 else 0
        print(f"  {s:<15} {counts['correct']}/{counts['total']} = {acc:.1%}")

    # Per-difficulty breakdown
    print("\nPer-difficulty breakdown:")
    by_diff: dict[str, dict[str, int]] = {}
    for d in report.details:
        diff = d.get("difficulty", "unknown")
        if diff not in by_diff:
            by_diff[diff] = {"total": 0, "correct": 0}
        by_diff[diff]["total"] += 1
        if d["correct"]:
            by_diff[diff]["correct"] += 1
    for diff, counts in sorted(by_diff.items()):
        acc = counts["correct"] / counts["total"] if counts["total"] > 0 else 0
        print(f"  {diff:<10} {counts['correct']}/{counts['total']} = {acc:.1%}")

    # Write report
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    tag = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    report_path = REPORT_DIR / f"skill_router_{tag}.json"
    summary = {
        "mode": "skill_router",
        "total": report.total,
        "correct": report.correct,
        "accuracy": report.correct / report.total if report.total > 0 else 0,
        "skill_accuracy": simple_correct / simple_total if simple_total > 0 else 0,
        "out_of_scope_accuracy": (
            report.out_of_scope_correct / report.out_of_scope_total
            if report.out_of_scope_total > 0 else 0
        ),
        "fallback_rate": report.fallback_count / report.total if report.total > 0 else 0,
        "llm_failure_rate": report.llm_failed_count / report.total if report.total > 0 else 0,
        "elapsed_sec": elapsed,
        "per_scenario": {
            s: {"accuracy": c["correct"] / c["total"]} for s, c in report.per_scenario.items()
        },
        "per_difficulty": {
            d: {"accuracy": c["correct"] / c["total"]} for d, c in by_diff.items()
        },
        "details": report.details,
    }
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nReport: {report_path}")

    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Skill Router accuracy benchmark")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--scenario", type=str, default=None)
    parser.add_argument("--difficulty", type=str, default=None)
    return parser


async def amain() -> None:
    args = build_parser().parse_args()
    await run_router_benchmark(args)


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
