"""Fast/Deep 端到端诊断评测。

直接调用 LangGraph，读取最终 state：
- Fast: response + past_steps
- Deep: response + rca + evidences

所有运行强制 read_only。评测不会提交后台任务、写 Postgres 或执行处置。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from loguru import logger

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BENCH_DIR = ROOT / "benchmark"
REPORT_DIR = BENCH_DIR / "reports"
EVAL_FILE = BENCH_DIR / "diagnosis_e2e_10.jsonl"


def dataset_fingerprint(rows: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        rows,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def load_rows(limit: int | None = None, ids: str | None = None) -> list[dict[str, Any]]:
    selected = {item.strip() for item in (ids or "").split(",") if item.strip()}
    rows: list[dict[str, Any]] = []
    with EVAL_FILE.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if selected and row.get("id") not in selected:
                continue
            rows.append(row)
            if limit and len(rows) >= limit:
                break
    return rows


def normalize(text: Any) -> str:
    return " ".join(str(text or "").lower().split())


def group_coverage(text: str, groups: list[list[str]]) -> tuple[int, int, float]:
    """每组内 OR、组间分别计数，返回命中数/总数/覆盖率。"""
    body = normalize(text)
    matched = sum(
        1
        for alternatives in groups
        if any(normalize(term) in body for term in alternatives)
    )
    total = len(groups)
    return matched, total, matched / total if total else 0.0


def extract_fast_root_cause(report: str) -> str:
    """优先截取 Fast 报告的根因章节，格式漂移时回退到报告前 1200 字。"""
    match = re.search(
        r"(?ims)^#{1,3}\s*"
        r"(?:[一二三四五六七八九十0-9]+[、.．]\s*)?"
        r"(?:根因(?:分析|判断)?|故障原因|结论)\s*$\s*"
        r"(.+?)(?=^#{1,3}\s|\Z)",
        report,
    )
    return (match.group(1) if match else report[:1200]).strip()


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, math.ceil(pct * len(ordered)) - 1))
    return ordered[rank]


class UsageTracker(BaseCallbackHandler):
    """从每次 LangChain LLMResult 中累计 Provider 返回的真实 usage。"""

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.llm_calls = 0
        self._lock = threading.Lock()

    @staticmethod
    def _message_usage(response: Any) -> dict[str, Any]:
        for generation_group in getattr(response, "generations", None) or []:
            for generation in generation_group or []:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None) or {}
                if usage:
                    return dict(usage)
                metadata = getattr(message, "response_metadata", None) or {}
                raw = metadata.get("token_usage") if isinstance(metadata, dict) else None
                if raw:
                    return {
                        "input_tokens": raw.get("prompt_tokens", 0),
                        "output_tokens": raw.get("completion_tokens", 0),
                        "total_tokens": raw.get("total_tokens", 0),
                    }
        return {}

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        usage = self._message_usage(response)
        if not usage:
            raw = (getattr(response, "llm_output", None) or {}).get("token_usage") or {}
            usage = {
                "input_tokens": raw.get("prompt_tokens", 0),
                "output_tokens": raw.get("completion_tokens", 0),
                "total_tokens": raw.get("total_tokens", 0),
            }
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
        with self._lock:
            self.llm_calls += 1
            self.input_tokens += input_tokens
            self.output_tokens += output_tokens
            self.total_tokens += total_tokens

    def payload(self) -> dict[str, Any]:
        return {
            "available": self.total_tokens > 0,
            "llm_calls": self.llm_calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }


def _deep_evidence_text(evidences: list[dict[str, Any]]) -> str:
    return "\n".join(
        f"{item.get('source', '')} {item.get('type', '')} {item.get('summary', '')}"
        for item in evidences
    )


def _fast_evidence_text(past_steps: list[Any]) -> str:
    lines: list[str] = []
    for item in past_steps:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            lines.append(f"{item[0]}\n{item[1]}")
        else:
            lines.append(str(item))
    return "\n".join(lines)


def score_state(row: dict[str, Any], mode: str, state: dict[str, Any]) -> dict[str, Any]:
    report = str(state.get("response") or "")
    expected_root = row.get("expected_root_cause_groups") or []
    evidence_groups = row.get("evidence_term_groups") or []

    if mode == "deep":
        rca = state.get("rca") or {}
        root_cause = str(rca.get("root_cause") or "")
        evidences = list(state.get("evidences") or [])
        evidence_text = _deep_evidence_text(evidences)
        cited_ids = [str(item) for item in rca.get("supporting_evidence_ids") or []]
        valid_ids = {f"ev_{idx}" for idx in range(len(evidences))}
        valid_citations = [item for item in cited_ids if item in valid_ids]
        cited_evidence: list[str] = []
        for item in valid_citations:
            evidence = evidences[int(item.split("_", 1)[1])]
            cited_evidence.append(str(evidence.get("summary") or ""))
        citation_validity = len(valid_citations) / len(cited_ids) if cited_ids else 0.0
        _, _, cited_evidence_coverage = group_coverage(
            "\n".join(cited_evidence),
            evidence_groups,
        )
        citation_correctness = citation_validity * cited_evidence_coverage
    else:
        root_cause = extract_fast_root_cause(report)
        evidence_text = _fast_evidence_text(list(state.get("past_steps") or []))
        cited_ids = re.findall(r"\bev_\d+\b", report)
        # Fast state 当前没有稳定 Evidence ID，因此出现 ev_X 也无法验证到实体。
        citation_validity = 0.0
        citation_correctness = 0.0

    root_matched, root_total, root_coverage = group_coverage(root_cause, expected_root)
    evidence_matched, evidence_total, evidence_coverage = group_coverage(
        evidence_text,
        evidence_groups,
    )
    return {
        "root_cause": root_cause,
        "root_cause_top1_correct": root_total > 0 and root_matched == root_total,
        "root_cause_group_coverage": root_coverage,
        "evidence_group_coverage": evidence_coverage,
        "evidence_groups_matched": evidence_matched,
        "evidence_groups_total": evidence_total,
        "citation_count": len(cited_ids),
        "citation_validity": citation_validity,
        "citation_correctness": citation_correctness,
        "report_chars": len(report),
        "report": report,
    }


async def load_graphs(modes: list[str]) -> dict[str, Any]:
    from app.orchestration.diagnosis_runner import (
        get_deep_diagnosis_graph,
        get_diagnosis_graph,
    )

    graphs: dict[str, Any] = {}
    if "fast" in modes:
        graphs["fast"] = await get_diagnosis_graph()
    if "deep" in modes:
        graphs["deep"] = await get_deep_diagnosis_graph()
    return graphs


async def run_one(
    *,
    row: dict[str, Any],
    mode: str,
    graph: Any,
    timeout_sec: float,
    recursion_limit: int,
) -> dict[str, Any]:
    tracker = UsageTracker()
    graph_input = {
        "input": str(row["input"]),
        "diagnosis_mode": mode,
        "requested_diagnosis_mode": mode,
        "permission_mode": "read_only",
        "alert_signature": "",
    }
    started = time.perf_counter()
    state = await asyncio.wait_for(
        graph.ainvoke(
            graph_input,
            config={
                "recursion_limit": recursion_limit,
                "callbacks": [tracker],
            },
        ),
        timeout=timeout_sec,
    )
    latency_sec = time.perf_counter() - started
    return {
        "id": row.get("id"),
        "scenario": row.get("scenario"),
        "mode": mode,
        "input": row.get("input"),
        "latency_sec": latency_sec,
        "usage": tracker.payload(),
        "score": score_state(row, mode, state),
    }


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def summarize(details: list[dict[str, Any]], errors: list[dict[str, Any]]) -> dict[str, Any]:
    modes = sorted({str(item["mode"]) for item in details})
    per_mode: dict[str, Any] = {}
    for mode in modes:
        rows = [item for item in details if item["mode"] == mode]
        latencies = [float(item["latency_sec"]) for item in rows]
        top1 = [bool(item["score"]["root_cause_top1_correct"]) for item in rows]
        evidence = [float(item["score"]["evidence_group_coverage"]) for item in rows]
        citations = [float(item["score"]["citation_correctness"]) for item in rows]
        validities = [float(item["score"]["citation_validity"]) for item in rows]
        total_tokens = sum(int(item["usage"]["total_tokens"]) for item in rows)
        usage_available = sum(1 for item in rows if item["usage"]["available"])
        per_mode[mode] = {
            "rows_succeeded": len(rows),
            "root_cause_top1_accuracy": sum(top1) / len(top1) if top1 else 0.0,
            "mean_evidence_group_coverage": sum(evidence) / len(evidence) if evidence else 0.0,
            "mean_citation_validity": sum(validities) / len(validities) if validities else 0.0,
            "mean_citation_correctness": sum(citations) / len(citations) if citations else 0.0,
            "latency_mean_sec": sum(latencies) / len(latencies) if latencies else 0.0,
            "latency_p50_sec": percentile(latencies, 0.50),
            "latency_p95_sec": percentile(latencies, 0.95),
            "token_usage_rows": usage_available,
            "total_tokens": total_tokens if usage_available else None,
            "mean_tokens": total_tokens / usage_available if usage_available else None,
        }
    return {
        "per_mode": per_mode,
        "errors": errors,
    }


def rescore_saved_report(source: Path) -> Path:
    payload = json.loads(source.read_text(encoding="utf-8"))
    rows_by_id = {str(row["id"]): row for row in load_rows()}
    details = list(payload.get("details") or [])
    for item in details:
        row = rows_by_id.get(str(item.get("id")))
        score = item.get("score") or {}
        if row is None or item.get("mode") != "fast":
            continue
        root_cause = extract_fast_root_cause(str(score.get("report") or ""))
        matched, total, coverage = group_coverage(
            root_cause,
            row.get("expected_root_cause_groups") or [],
        )
        score["root_cause"] = root_cause
        score["root_cause_top1_correct"] = total > 0 and matched == total
        score["root_cause_group_coverage"] = coverage

    payload["rescored_from"] = str(source)
    payload["rescored_at"] = datetime.now(timezone.utc).isoformat()
    payload.setdefault("run_config", {})["scoring_version"] = 2
    payload.update(summarize(details, list(payload.get("errors") or [])))
    tag = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    report = REPORT_DIR / f"diagnosis_e2e_rescored_{tag}.json"
    write_json_atomic(report, payload)
    return report


async def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    from app.config import settings
    from app.runtime.agent_harness import get_agent_harness

    rows = load_rows(limit=args.limit, ids=args.ids)
    if not rows:
        raise SystemExit("No diagnosis evaluation rows selected.")
    modes = [item.strip() for item in args.modes.split(",") if item.strip()]
    if not modes or any(item not in {"fast", "deep"} for item in modes):
        raise SystemExit("--modes must contain fast and/or deep")
    if args.no_rerank:
        settings.rag_rerank_enabled = False

    checkpoint = Path(args.checkpoint) if args.checkpoint else REPORT_DIR / "diagnosis_checkpoint.json"
    if not checkpoint.is_absolute():
        checkpoint = ROOT / checkpoint
    run_config = {
        "scoring_version": 2,
        "dataset_ids": [row["id"] for row in rows],
        "dataset_sha256": dataset_fingerprint(rows),
        "modes": modes,
        "model": settings.dashscope_chat_model,
        "router_model": settings.dashscope_router_model,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.local_embedding_model,
        "rerank_enabled": settings.rag_rerank_enabled,
        "rerank_provider": settings.rag_rerank_provider,
        "rerank_model": settings.rag_rerank_model,
        "timeout_sec": args.timeout,
    }
    details: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if args.resume and checkpoint.exists():
        previous = json.loads(checkpoint.read_text(encoding="utf-8"))
        if previous.get("run_config") != run_config:
            raise SystemExit("Diagnosis checkpoint configuration does not match this run.")
        details = list(previous.get("details") or [])
        print(f"resume checkpoint: {checkpoint} | completed={len(details)}/{len(rows) * len(modes)}")

    completed = {(str(item.get("id")), str(item.get("mode"))) for item in details}
    graphs = await load_graphs(modes)
    recursion_limit = get_agent_harness().graph_recursion_limit()
    total = len(rows) * len(modes)
    started = time.perf_counter()

    for row in rows:
        for mode in modes:
            key = (str(row["id"]), mode)
            if key in completed:
                continue
            index = len(details) + len(errors) + 1
            try:
                item = await run_one(
                    row=row,
                    mode=mode,
                    graph=graphs[mode],
                    timeout_sec=args.timeout,
                    recursion_limit=recursion_limit,
                )
                details.append(item)
                score = item["score"]
                usage = item["usage"]
                print(
                    f"[{index:02d}/{total:02d}] {mode:<4} "
                    f"top1={int(score['root_cause_top1_correct'])} "
                    f"evidence={score['evidence_group_coverage']:.2f} "
                    f"citation={score['citation_correctness']:.2f} "
                    f"latency={item['latency_sec']:.1f}s "
                    f"tokens={usage['total_tokens'] if usage['available'] else 'NA'} "
                    f"| {row['scenario']}"
                )
            except Exception as exc:  # noqa: BLE001 - 单个 case/mode 必须隔离失败
                error = {
                    "id": row.get("id"),
                    "scenario": row.get("scenario"),
                    "mode": mode,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                errors.append(error)
                print(
                    f"[{index:02d}/{total:02d}] {mode:<4} ERROR "
                    f"{type(exc).__name__}: {exc}"
                )
            payload = {
                "mode": "diagnosis_e2e_checkpoint",
                "run_config": run_config,
                "elapsed_sec": time.perf_counter() - started,
                "details": details,
                **summarize(details, errors),
            }
            write_json_atomic(checkpoint, payload)

    summary = {
        "mode": "diagnosis_e2e",
        "run_config": run_config,
        "rows_requested": len(rows),
        "runs_requested": total,
        "runs_succeeded": len(details),
        "runs_failed": len(errors),
        "elapsed_sec": time.perf_counter() - started,
        "details": details,
        **summarize(details, errors),
    }
    tag = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    report = REPORT_DIR / f"diagnosis_e2e_{tag}.json"
    write_json_atomic(report, summary)
    write_json_atomic(checkpoint, {"complete": not errors, **summary})
    print(json.dumps(summary["per_mode"], ensure_ascii=False, indent=2))
    print(f"checkpoint: {checkpoint}")
    print(f"report: {report}")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fast/Deep diagnosis E2E benchmark")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--ids", type=str, default=None)
    parser.add_argument("--modes", type=str, default="fast,deep")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="Disable the currently incompatible local reranker for this run",
    )
    parser.add_argument("--checkpoint", type=str, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--rescore-report",
        type=str,
        default=None,
        help="Recompute deterministic scores from a saved report without LLM calls",
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="WARNING",
        help="Application log level during benchmark execution",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.rescore_report:
        source = Path(args.rescore_report)
        if not source.is_absolute():
            source = ROOT / source
        report = rescore_saved_report(source)
        print(f"rescored report: {report}")
        return
    logger.remove()
    logger.add(sys.stderr, level=args.log_level)
    asyncio.run(run_benchmark(args))


if __name__ == "__main__":
    main()
