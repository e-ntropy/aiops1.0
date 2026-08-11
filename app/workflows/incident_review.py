"""基于 Postgres 事实生成事故复盘，不依赖模型补写时间线。"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

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


class IncidentReviewResult(BaseModel):
    state: WorkflowState
    source_run_id: str = ""
    event_count: int = 0
    decision_count: int = 0


def _load_json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


async def execute_incident_review(state: WorkflowState) -> IncidentReviewResult:
    validate_phase_transition(state.phase, WorkflowPhase.EXECUTING)
    state.phase = WorkflowPhase.EXECUTING
    state.transitions.append(
        WorkflowTransition(
            from_phase=WorkflowPhase.READY,
            to_phase=WorkflowPhase.EXECUTING,
            reason="incident_fact_review_started",
        )
    )
    pool = await get_pool()
    async with pool.acquire() as conn:
        source = await conn.fetchrow(
            """
            SELECT run_id, state FROM workflow_runs
            WHERE run_id <> $1
              AND (session_id = $2 OR ($3 <> '' AND incident_id = $3))
              AND state #>> '{lifecycle,stage}' = 'closed'
            ORDER BY updated_at DESC LIMIT 1
            """,
            state.run_id,
            state.session_id,
            state.incident_id,
        )
        if source is None:
            source = await conn.fetchrow(
                """
                SELECT run_id, state FROM workflow_runs
                WHERE run_id <> $1
                  AND state #>> '{lifecycle,stage}' = 'closed'
                ORDER BY updated_at DESC LIMIT 1
                """,
                state.run_id,
            )
        if source is None:
            state.phase = WorkflowPhase.FAILED
            state.terminal_reason = "Postgres 中没有已关闭事故，无法生成事实复盘"
            state.transitions.append(
                WorkflowTransition(
                    from_phase=WorkflowPhase.EXECUTING,
                    to_phase=WorkflowPhase.FAILED,
                    reason="closed_incident_not_found",
                )
            )
            return IncidentReviewResult(state=state)
        source_run_id = str(source["run_id"])
        source_state = WorkflowState.model_validate(_load_json(source["state"]))
        events = await conn.fetch(
            """
            SELECT revision, event_type, from_phase, to_phase, actor, created_at
            FROM workflow_events WHERE run_id = $1
            ORDER BY revision, created_at
            """,
            source_run_id,
        )
        decisions = await conn.fetch(
            """
            SELECT decision_type, status, actor, note, created_at
            FROM human_decisions WHERE run_id = $1 ORDER BY created_at
            """,
            source_run_id,
        )
        experiences = await conn.fetch(
            """
            SELECT outcome, summary, root_cause, status
            FROM experience_records WHERE source_run_id = $1 ORDER BY created_at
            """,
            source_run_id,
        )

    timeline = [
        f"- {row['created_at']} · r{row['revision']} · {row['event_type']} · "
        f"{row['from_phase'] or '-'} → {row['to_phase'] or '-'} · actor={row['actor']}"
        for row in events
    ]
    decision_lines = [
        f"- {row['decision_type']}: {row['status']} · actor={row['actor']}"
        + (f" · {row['note']}" if row["note"] else "")
        for row in decisions
    ]
    root_cause = (
        source_state.lifecycle.diagnosis.confirmed_root_cause
        or source_state.outcome.root_cause
    )
    report = "\n".join(
        [
            "# 事故复盘",
            "",
            f"- Source Run: `{source_run_id}`",
            f"- Incident: `{source_state.incident_id or '-'}`",
            f"- 确认根因: {root_cause or '未记录'}",
            f"- 恢复验证: {source_state.lifecycle.verification.summary or '未记录'}",
            "",
            "## 时间线",
            *(timeline or ["- 无事件记录"]),
            "",
            "## 人工决策",
            *(decision_lines or ["- 无人工决策记录"]),
            "",
            "## 成败经验",
            *(
                [f"- {row['outcome']}/{row['status']}: {row['summary']}" for row in experiences]
                or ["- 无经验记录"]
            ),
            "",
            "## 后续行动",
            *(
                [f"- {item}" for item in source_state.outcome.recommendations]
                or ["- 持续通过评测样本回归该事故路径"]
            ),
        ]
    )
    state.evidence.append(
        EvidenceItem(
            run_id=state.run_id,
            incident_id=source_state.incident_id,
            source="postgres_incident_facts",
            type="incident_review_reference",
            status=EvidenceStatus.REFERENCE,
            summary=f"基于 {len(events)} 个事件和 {len(decisions)} 个人工决策生成复盘",
            content={"source_run_id": source_run_id},
            confidence=1.0,
        )
    )
    state.outcome.summary = "已基于 Postgres 事实生成事故复盘"
    state.outcome.report_markdown = report
    state.outcome.root_cause = root_cause
    state.outcome.confidence = 1.0
    state.outcome.next_action = "评审行动项并纳入回归评测"
    state.phase = WorkflowPhase.COMPLETED
    state.transitions.append(
        WorkflowTransition(
            from_phase=WorkflowPhase.EXECUTING,
            to_phase=WorkflowPhase.COMPLETED,
            reason="incident_fact_review_completed",
        )
    )
    attach_memory_write_policy(state)
    return IncidentReviewResult(
        state=state,
        source_run_id=source_run_id,
        event_count=len(events),
        decision_count=len(decisions),
    )
