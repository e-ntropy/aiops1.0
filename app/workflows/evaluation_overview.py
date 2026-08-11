"""汇总可复现评测资产与事故闭环生成的评测样本。"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from app.db.postgres import get_pool
from app.workflows.memory_policy import attach_memory_write_policy
from app.workflows.models import (
    EvidenceItem,
    EvidenceStatus,
    WorkflowPhase,
    WorkflowState,
    WorkflowTransition,
)
from app.workflows.policies import validate_phase_transition

_BENCHMARK_ROOT = Path(__file__).resolve().parents[2] / "benchmark"
_DATASETS = {
    "workflow_contract": "workflow_contract_eval.jsonl",
    "lifecycle_contract": "lifecycle_contract_eval.jsonl",
    "diagnosis_fixture": "diagnosis_fixture_eval.jsonl",
    "memory_governance": "memory_governance_eval.jsonl",
    "tool_safety": "tool_safety_eval.jsonl",
    "diagnosis_e2e": "diagnosis_e2e_10.jsonl",
    "skill_router": "skill_router_eval.jsonl",
    "retrieval": "retrieval_rk_50.jsonl",
    "rag_qa": "ragas_qa_50.jsonl",
}


class EvaluationOverviewResult(BaseModel):
    state: WorkflowState
    dataset_counts: dict[str, int] = Field(default_factory=dict)
    generated_sample_count: int = 0
    generated_by_capability: dict[str, int] = Field(default_factory=dict)


def _count_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open("r", encoding="utf-8") as stream:
        return sum(1 for line in stream if line.strip())


async def execute_evaluation_overview(state: WorkflowState) -> EvaluationOverviewResult:
    """只读展示评测资产，不在在线 Agent 中触发付费或破坏性 Benchmark。"""

    validate_phase_transition(state.phase, WorkflowPhase.EXECUTING)
    state.phase = WorkflowPhase.EXECUTING
    state.transitions.append(
        WorkflowTransition(
            from_phase=WorkflowPhase.READY,
            to_phase=WorkflowPhase.EXECUTING,
            reason="evaluation_inventory_started",
        )
    )
    dataset_counts = {
        name: _count_jsonl(_BENCHMARK_ROOT / filename)
        for name, filename in _DATASETS.items()
    }
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT capability_id, count(*) AS sample_count
            FROM evaluation_samples
            WHERE isolation_status = 'isolated'
            GROUP BY capability_id ORDER BY capability_id
            """
        )
    generated_by_capability = {
        str(row["capability_id"]): int(row["sample_count"]) for row in rows
    }
    generated_sample_count = sum(generated_by_capability.values())
    report = "\n".join(
        [
            "# 评测资产摘要",
            "",
            "## 版本化离线数据集",
            *[f"- {name}: {count} 条" for name, count in dataset_counts.items()],
            "",
            "## 事故闭环生成样本",
            f"- 已隔离样本合计: {generated_sample_count} 条",
            *(
                [f"- {name}: {count} 条" for name, count in generated_by_capability.items()]
                or ["- 暂无已关闭事故生成的隔离样本"]
            ),
            "",
            "## 可复现命令",
            "```bash",
            "python benchmark/run_benchmark.py workflow --enforce",
            "python benchmark/run_benchmark.py fixture --enforce",
            "python benchmark/run_benchmark.py memory --enforce",
            "python benchmark/run_benchmark.py tool --enforce",
            "python benchmark/run_benchmark.py retrieval --k 3",
            "```",
            "",
            "> 在线 Agent 只展示资产。需要 Provider 的评测必须由操作者确认凭据、费用和数据范围后离线运行。",
        ]
    )
    state.evidence.append(
        EvidenceItem(
            run_id=state.run_id,
            source="evaluation_inventory",
            type="evaluation_asset_reference",
            status=EvidenceStatus.REFERENCE,
            summary=(
                f"发现 {sum(dataset_counts.values())} 条版本化样本和 "
                f"{generated_sample_count} 条事故闭环隔离样本"
            ),
            content={
                "dataset_counts": dataset_counts,
                "generated_by_capability": generated_by_capability,
            },
            confidence=1.0,
        )
    )
    state.outcome.summary = "已汇总可复现评测资产"
    state.outcome.report_markdown = report
    state.outcome.confidence = 1.0
    state.outcome.next_action = "在隔离环境运行所需评测并保存带时间戳报告"
    state.phase = WorkflowPhase.COMPLETED
    state.transitions.append(
        WorkflowTransition(
            from_phase=WorkflowPhase.EXECUTING,
            to_phase=WorkflowPhase.COMPLETED,
            reason="evaluation_inventory_completed",
        )
    )
    attach_memory_write_policy(state)
    return EvaluationOverviewResult(
        state=state,
        dataset_counts=dataset_counts,
        generated_sample_count=generated_sample_count,
        generated_by_capability=generated_by_capability,
    )
