"""Postgres 事实源：统一 Workflow、事件、人工决策与学习闭环。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.db_utils import json_dump, new_id
from app.db.postgres import get_pool
from app.workflows.incident_lifecycle import IncidentClosureResult
from app.workflows.memory_retrieval import select_governed_memories
from app.workflows.models import (
    FailureRecord,
    LifecycleStage,
    WorkflowIntent,
    WorkflowPhase,
    WorkflowState,
    WorkflowTransition,
)


class WorkflowNotFoundError(LookupError):
    """请求的持久化 Workflow 不存在。"""


class WorkflowConflictError(RuntimeError):
    """expected_revision 已过期，拒绝静默覆盖更新。"""

    def __init__(self, run_id: str, expected: int, actual: int | None) -> None:
        self.run_id = run_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Workflow {run_id} 版本冲突: expected={expected}, actual={actual}"
        )


class WorkflowLeaseError(RuntimeError):
    """Workflow 正由另一个执行者处理。"""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        super().__init__(f"Workflow {run_id} 已被其他执行者 claim")


def _json_load(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def _value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _scope_key(state: WorkflowState) -> str:
    scope = state.scope
    return "|".join(
        part
        for part in (
            _value(scope.kind),
            scope.environment,
            scope.service,
            scope.resource_id,
            scope.instance,
        )
        if part
    )


def _latest_transition(state: WorkflowState) -> tuple[str, str]:
    if not state.transitions:
        return "", _value(state.phase)
    item = state.transitions[-1]
    return _value(item.from_phase), _value(item.to_phase)


class WorkflowRepository:
    """WorkflowState 的唯一持久化入口。

    `workflow_runs.state` 保存可恢复快照；`workflow_events` 保存追加式审计事件。
    所有更新使用 revision CAS，避免两个浏览器或 Worker 静默覆盖彼此状态。
    """

    async def create(self, state: WorkflowState, *, actor: str = "system") -> WorkflowState:
        if state.revision != 0:
            raise ValueError("只有 revision=0 的 WorkflowState 可以创建")
        pool = await get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._maintain_memory_records(conn)
            if (
                state.query.primary_intent == WorkflowIntent.FAULT_DIAGNOSIS
                and not state.incident_id
            ):
                await self._create_manual_incident(conn, state)
            await self._recall_memories(conn, state)
            await self._recall_profiles(conn, state, actor=actor)
            state.revision = 1
            await conn.execute(
                """
                INSERT INTO workflow_runs (
                    run_id, schema_version, revision, session_id, incident_id,
                    incident_group_id, capability_id, execution_strategy, phase, scope_kind,
                    state, terminal_reason, completed_at
                )
                VALUES (
                    $1, $2, 1, $3, NULLIF($4, ''), NULLIF($5, ''), $6, $7, $8, $9,
                    $10::jsonb, $11,
                    CASE WHEN $8 IN ('completed', 'failed', 'cancelled') THEN now() END
                )
                """,
                state.run_id,
                state.state_version,
                state.session_id,
                state.incident_id,
                state.incident_group_id,
                state.capability_id,
                state.execution_strategy,
                _value(state.phase),
                _value(state.scope.kind),
                json_dump(state.model_dump(mode="json")),
                state.terminal_reason,
            )
            await self._append_event(
                conn,
                state,
                revision=1,
                event_type="workflow_created",
                actor=actor,
                payload={"intent": _value(state.query.primary_intent)},
            )
            await self._persist_derived_records(conn, state)
        return state

    async def get(self, run_id: str) -> WorkflowState:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT state, revision FROM workflow_runs WHERE run_id = $1",
                run_id,
            )
        if row is None:
            raise WorkflowNotFoundError(run_id)
        payload = dict(_json_load(row["state"]) or {})
        payload["revision"] = int(row["revision"])
        return WorkflowState.model_validate(payload)

    async def save(
        self,
        state: WorkflowState,
        *,
        expected_revision: int,
        event_type: str,
        actor: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> WorkflowState:
        pool = await get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._save_with_conn(
                conn,
                state,
                expected_revision=expected_revision,
                event_type=event_type,
                actor=actor,
                payload=payload or {},
            )
            await self._persist_derived_records(conn, state)
        return state

    async def claim_execution(
        self,
        state: WorkflowState,
        *,
        expected_revision: int,
        actor: str,
        lease_seconds: int = 900,
    ) -> WorkflowState:
        """原子 claim 一次执行，阻止相同 revision 被并行执行。"""
        if lease_seconds < 30:
            raise ValueError("lease_seconds 不能小于 30")
        candidate = state.model_copy(deep=True)
        candidate.revision = expected_revision + 1
        pool = await get_pool()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE workflow_runs
                SET revision = revision + 1,
                    state = $4::jsonb,
                    lease_owner = $3,
                    lease_expires_at = now() + ($5 * INTERVAL '1 second'),
                    updated_at = now()
                WHERE run_id = $1
                  AND revision = $2
                  AND (lease_expires_at IS NULL OR lease_expires_at <= now())
                RETURNING revision
                """,
                state.run_id,
                expected_revision,
                actor,
                json_dump(candidate.model_dump(mode="json")),
                lease_seconds,
            )
            if row is None:
                current = await conn.fetchrow(
                    """
                    SELECT revision, lease_owner, lease_expires_at
                    FROM workflow_runs WHERE run_id = $1
                    """,
                    state.run_id,
                )
                if current is None:
                    raise WorkflowNotFoundError(state.run_id)
                if int(current["revision"]) != expected_revision:
                    raise WorkflowConflictError(
                        state.run_id,
                        expected_revision,
                        int(current["revision"]),
                    )
                raise WorkflowLeaseError(state.run_id)
            candidate.revision = int(row["revision"])
            await self._append_event(
                conn,
                candidate,
                revision=candidate.revision,
                event_type="execution_claimed",
                actor=actor,
                payload={"lease_seconds": lease_seconds},
            )
            state.revision = candidate.revision
        return state

    async def record_execution_failure(
        self,
        state: WorkflowState,
        *,
        expected_revision: int,
        actor: str,
        error: BaseException,
        cancelled: bool = False,
    ) -> WorkflowState:
        """释放执行租约并把异常转换为可恢复的事实记录。"""
        from_phase = state.phase
        target = WorkflowPhase.CANCELLED if cancelled else WorkflowPhase.FAILED
        if state.phase != target:
            state.phase = target
            state.transitions.append(
                WorkflowTransition(
                    from_phase=from_phase,
                    to_phase=target,
                    reason="execution_cancelled" if cancelled else "execution_exception",
                )
            )
        message = f"{type(error).__name__}: {error}"[:2000]
        retryable = not cancelled and isinstance(
            error,
            (TimeoutError, ConnectionError, OSError),
        )
        state.terminal_reason = message
        state.failures.append(
            FailureRecord(
                stage="capability_execution",
                error_type=type(error).__name__,
                message=str(error)[:2000],
                retryable=retryable,
                fallback_action=(
                    "retry_from_persisted_state"
                    if retryable
                    else "resubmit"
                    if cancelled
                    else "manual_review"
                ),
            )
        )
        return await self.save(
            state,
            expected_revision=expected_revision,
            event_type="execution_cancelled" if cancelled else "execution_failed",
            actor=actor,
            payload={"error_type": type(error).__name__},
        )

    async def save_human_decision(
        self,
        state: WorkflowState,
        *,
        expected_revision: int,
        decision_type: str,
        status: str,
        actor: str,
        note: str = "",
        payload: dict[str, Any] | None = None,
    ) -> WorkflowState:
        decision_payload = payload or {}
        pool = await get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._save_with_conn(
                conn,
                state,
                expected_revision=expected_revision,
                event_type=f"human_{decision_type}",
                actor=actor,
                payload={"status": status, **decision_payload},
            )
            await conn.execute(
                    """
                    INSERT INTO human_decisions (
                        id, run_id, incident_id, decision_type, status,
                        actor, note, payload
                    )
                    VALUES ($1, $2, NULLIF($3, ''), $4, $5, $6, $7, $8::jsonb)
                    """,
                    new_id("decision"),
                    state.run_id,
                    state.incident_id,
                    decision_type,
                    status,
                    actor,
                    note,
                    json_dump(decision_payload),
            )
            await self._persist_derived_records(conn, state)
        return state

    async def close_with_learning(
        self,
        result: IncidentClosureResult,
        *,
        expected_revision: int,
        actor: str,
    ) -> IncidentClosureResult:
        state = result.state
        sample = result.eval_sample
        if state.lifecycle.stage != LifecycleStage.CLOSED:
            raise ValueError("只有 CLOSED Workflow 可以事务沉淀")
        pool = await get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._save_with_conn(
                conn,
                state,
                expected_revision=expected_revision,
                event_type="incident_closed",
                actor=actor,
                payload={"evaluation_sample_id": sample.id},
            )
            await conn.execute(
                    """
                    INSERT INTO human_decisions (
                        id, run_id, incident_id, decision_type, status,
                        actor, note, payload
                    )
                    VALUES ($1, $2, NULLIF($3, ''), 'incident_closure',
                            'confirmed', $4, '', $5::jsonb)
                    """,
                    new_id("decision"),
                    state.run_id,
                    state.incident_id,
                    actor,
                    json_dump({"redaction_passed": True}),
            )
            memory_id = await self._persist_verified_memory(conn, state, sample.query)
            await self._persist_success_experience(conn, state)
            await self._persist_eval_sample(conn, result)
            await self._update_profiles(conn, state, actor, memory_id)
            await self._close_incident_facts(conn, state)
        return result

    async def list_events(self, run_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        limit = max(1, min(limit, 1000))
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM workflow_events
                WHERE run_id = $1
                ORDER BY revision, created_at
                LIMIT $2
                """,
                run_id,
                limit,
            )
        return [dict(row) for row in rows]

    async def _save_with_conn(
        self,
        conn: Any,
        state: WorkflowState,
        *,
        expected_revision: int,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> None:
        if expected_revision < 1:
            raise ValueError("expected_revision 必须大于 0")
        candidate = state.model_copy(deep=True)
        candidate.revision = expected_revision + 1
        row = await conn.fetchrow(
            """
            UPDATE workflow_runs
            SET revision = revision + 1,
                schema_version = $3,
                session_id = $4,
                incident_id = NULLIF($5, ''),
                incident_group_id = NULLIF($6, ''),
                capability_id = $7,
                execution_strategy = $8,
                phase = $9,
                scope_kind = $10,
                state = $11::jsonb,
                terminal_reason = $12,
                lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = now(),
                completed_at = CASE
                    WHEN $9 IN ('completed', 'failed', 'cancelled')
                    THEN COALESCE(completed_at, now())
                    ELSE NULL
                END
            WHERE run_id = $1 AND revision = $2
            RETURNING revision
            """,
            state.run_id,
            expected_revision,
            state.state_version,
            state.session_id,
            state.incident_id,
            state.incident_group_id,
            state.capability_id,
            state.execution_strategy,
            _value(state.phase),
            _value(state.scope.kind),
            json_dump(candidate.model_dump(mode="json")),
            state.terminal_reason,
        )
        if row is None:
            actual = await conn.fetchval(
                "SELECT revision FROM workflow_runs WHERE run_id = $1",
                state.run_id,
            )
            if actual is None:
                raise WorkflowNotFoundError(state.run_id)
            raise WorkflowConflictError(state.run_id, expected_revision, int(actual))
        new_revision = int(row["revision"])
        candidate.revision = new_revision
        await self._append_event(
            conn,
            candidate,
            revision=new_revision,
            event_type=event_type,
            actor=actor,
            payload=payload,
        )
        state.revision = new_revision

    async def _append_event(
        self,
        conn: Any,
        state: WorkflowState,
        *,
        revision: int,
        event_type: str,
        actor: str,
        payload: dict[str, Any],
    ) -> None:
        from_phase, to_phase = _latest_transition(state)
        await conn.execute(
            """
            INSERT INTO workflow_events (
                id, run_id, revision, event_type, from_phase, to_phase,
                actor, payload
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb)
            """,
            new_id("wfev"),
            state.run_id,
            revision,
            event_type,
            from_phase,
            to_phase,
            actor,
            json_dump(payload),
        )

    async def _create_manual_incident(self, conn: Any, state: WorkflowState) -> None:
        group_id = new_id("ig")
        incident_id = new_id("inc")
        service = state.scope.service
        title = state.query.rewritten_query[:500]
        await conn.execute(
            """
            INSERT INTO incident_groups (
                id, correlation_key, status, severity, primary_service,
                summary, metadata
            )
            VALUES ($1, $2, 'open', 'warning', $3, $4, $5::jsonb)
            """,
            group_id,
            f"workflow:{state.run_id}",
            service,
            title,
            json_dump({"source": "workflow", "run_id": state.run_id}),
        )
        await conn.execute(
            """
            INSERT INTO incidents (
                id, incident_group_id, status, title, severity, service,
                started_at, metadata
            )
            VALUES ($1, $2, 'open', $3, 'warning', $4, now(), $5::jsonb)
            """,
            incident_id,
            group_id,
            title,
            service,
            json_dump({"source": "workflow", "run_id": state.run_id}),
        )
        state.incident_id = incident_id
        state.incident_group_id = group_id

    async def _recall_memories(self, conn: Any, state: WorkflowState) -> None:
        rows = await conn.fetch(
            """
            SELECT id, tier, memory_kind, status, content, confidence,
                   session_id, incident_id, service, scope_key,
                   redaction_passed, expires_at, superseded_by
            FROM memory_records
            WHERE superseded_by IS NULL
              AND (expires_at IS NULL OR expires_at > now())
              AND tier IN ('session', 'incident', 'verified_knowledge')
              AND (
                    (tier = 'session' AND status = 'active' AND session_id = $1)
                 OR (tier = 'incident' AND status IN ('active', 'verified')
                     AND incident_id = NULLIF($2, ''))
                 OR (tier = 'verified_knowledge' AND status = 'verified'
                     AND redaction_passed = true
                     AND (($3 <> '' AND service IN ('', $3)) OR ($3 = '' AND service = ''))
                     AND (($4 <> '' AND scope_key IN ('', $4)) OR ($4 = '' AND scope_key = '')))
              )
            ORDER BY
                CASE tier WHEN 'incident' THEN 0 WHEN 'verified_knowledge' THEN 1 ELSE 2 END,
                confidence DESC,
                updated_at DESC
            LIMIT 100
            """,
            state.session_id,
            state.incident_id,
            state.scope.service,
            _scope_key(state),
        )
        selected = select_governed_memories(
            [dict(row) for row in rows],
            session_id=state.session_id,
            incident_id=state.incident_id,
            service=state.scope.service,
            scope_key=_scope_key(state),
            limit=20,
        )
        recalled: list[dict[str, Any]] = []
        ids: list[str] = []
        for item in selected:
            item["content"] = _json_load(item.get("content"))
            recalled.append(item)
            ids.append(str(item["id"]))
            tier = str(item["tier"])
            if tier == "session":
                state.memory.session_refs.append(str(item["id"]))
            elif tier == "incident":
                state.memory.incident_refs.append(str(item["id"]))
            elif tier == "verified_knowledge":
                state.memory.knowledge_refs.append(str(item["id"]))
        state.memory.recalled_items = recalled
        if ids:
            await conn.execute(
                """
                UPDATE memory_records
                SET recall_count = recall_count + 1,
                    last_recalled_at = now(),
                    updated_at = now()
                WHERE id = ANY($1::text[])
                """,
                ids,
            )

    async def _recall_profiles(
        self,
        conn: Any,
        state: WorkflowState,
        *,
        actor: str,
    ) -> None:
        resource_key = state.scope.service or state.scope.resource_id
        resource_type = "service" if state.scope.service else "resource"
        rows = await conn.fetch(
            """
            SELECT id, subject_type, subject_key, revision, attributes
            FROM entity_profiles
            WHERE (subject_type = 'operator' AND subject_key = $1)
               OR (subject_type = $2 AND subject_key = NULLIF($3, ''))
            ORDER BY updated_at DESC
            LIMIT 10
            """,
            actor,
            resource_type,
            resource_key,
        )
        state.memory.profile_items = []
        for row in rows:
            item = dict(row)
            item["attributes"] = _json_load(item.get("attributes"))
            state.memory.profile_refs.append(str(item["id"]))
            state.memory.profile_items.append(item)

    async def _persist_derived_records(self, conn: Any, state: WorkflowState) -> None:
        await self._persist_session_memory(conn, state)
        if (
            state.phase == WorkflowPhase.COMPLETED
            and state.outcome.report_markdown
            and state.query.primary_intent
            in {
                WorkflowIntent.SYSTEM_INSPECTION,
                WorkflowIntent.FAULT_DIAGNOSIS,
                WorkflowIntent.OPTIMIZATION,
                WorkflowIntent.CAPACITY_PERFORMANCE,
                WorkflowIntent.INCIDENT_REVIEW,
            }
        ):
            await self._persist_candidate_memory(conn, state)
        if state.phase == WorkflowPhase.FAILED:
            await self._persist_failure_experience(conn, state)

    async def _persist_session_memory(self, conn: Any, state: WorkflowState) -> None:
        memory_id = new_id("mem")
        await conn.execute(
            """
            INSERT INTO memory_records (
                id, idempotency_key, tier, memory_kind, status, session_id,
                incident_id, service, scope_key, content, confidence,
                provenance, redaction_passed, expires_at
            )
            VALUES (
                $1, $2, 'session', 'workflow_summary', 'active', $3, $4,
                $5, $6, $7::jsonb, $8, $9::jsonb, false, $10
            )
            ON CONFLICT (idempotency_key) DO UPDATE SET
                content = EXCLUDED.content,
                confidence = EXCLUDED.confidence,
                expires_at = EXCLUDED.expires_at,
                updated_at = now()
            """,
            memory_id,
            f"session:{state.run_id}",
            state.session_id,
            state.incident_id,
            state.scope.service,
            _scope_key(state),
            json_dump(
                {
                    "intent": _value(state.query.primary_intent),
                    "summary": state.outcome.summary,
                    "phase": _value(state.phase),
                    "next_action": state.outcome.next_action,
                }
            ),
            state.outcome.confidence,
            json_dump({"run_id": state.run_id, "revision": state.revision}),
            datetime.now(timezone.utc) + timedelta(hours=24),
        )

    async def _persist_candidate_memory(self, conn: Any, state: WorkflowState) -> None:
        memory_id = new_id("mem")
        await conn.execute(
            """
            INSERT INTO memory_records (
                id, idempotency_key, tier, memory_kind, status, session_id,
                incident_id, service, scope_key, content, confidence,
                provenance, redaction_passed, expires_at
            )
            VALUES (
                $1, $2, 'candidate', 'diagnosis_candidate', 'candidate', $3,
                $4, $5, $6, $7::jsonb, $8, $9::jsonb, false, $10
            )
            ON CONFLICT (idempotency_key) DO UPDATE SET
                content = EXCLUDED.content,
                confidence = EXCLUDED.confidence,
                provenance = EXCLUDED.provenance,
                expires_at = EXCLUDED.expires_at,
                updated_at = now()
            """,
            memory_id,
            f"candidate:{state.run_id}",
            state.session_id,
            state.incident_id,
            state.scope.service,
            _scope_key(state),
            json_dump(
                {
                    "summary": state.outcome.summary,
                    "root_cause": state.outcome.root_cause,
                    "evidence_ids": [item.id for item in state.evidence],
                }
            ),
            state.outcome.confidence,
            json_dump({"run_id": state.run_id, "revision": state.revision}),
            datetime.now(timezone.utc) + timedelta(days=90),
        )

    async def _persist_failure_experience(self, conn: Any, state: WorkflowState) -> None:
        await conn.execute(
            """
            INSERT INTO experience_records (
                id, idempotency_key, source_run_id, incident_id, outcome,
                signature, summary, evidence_types, actions, applicability,
                confidence, status
            )
            VALUES (
                $1, $2, $3, $4, 'failure', $5, $6, $7::jsonb, '[]'::jsonb,
                $8::jsonb, 1.0, 'candidate'
            )
            ON CONFLICT (idempotency_key) DO UPDATE SET
                summary = EXCLUDED.summary,
                applicability = EXCLUDED.applicability,
                updated_at = now()
            """,
            new_id("exp"),
            f"failure:{state.run_id}",
            state.run_id,
            state.incident_id,
            f"{state.capability_id}:{state.terminal_reason[:200]}",
            state.terminal_reason or "工作流失败",
            json_dump(list(dict.fromkeys(item.type for item in state.evidence))),
            json_dump(
                {
                    "scope_kind": _value(state.scope.kind),
                    "failures": [item.model_dump(mode="json") for item in state.failures],
                }
            ),
        )

    async def _persist_verified_memory(
        self,
        conn: Any,
        state: WorkflowState,
        redacted_query: str,
    ) -> str:
        memory_id = new_id("mem")
        stored_memory_id = await conn.fetchval(
            """
            INSERT INTO memory_records (
                id, idempotency_key, tier, memory_kind, status, session_id,
                incident_id, service, scope_key, content, confidence,
                provenance, redaction_passed, verified_at
            )
            VALUES (
                $1, $2, 'verified_knowledge', 'confirmed_incident', 'verified',
                $3, $4, $5, $6, $7::jsonb, $8, $9::jsonb, true, now()
            )
            ON CONFLICT (idempotency_key) DO UPDATE SET
                status = 'verified',
                content = EXCLUDED.content,
                confidence = EXCLUDED.confidence,
                provenance = EXCLUDED.provenance,
                redaction_passed = true,
                verified_at = now(),
                updated_at = now()
            RETURNING id
            """,
            memory_id,
            f"verified:{state.run_id}",
            state.session_id,
            state.incident_id,
            state.scope.service,
            _scope_key(state),
            json_dump(
                {
                    "query": redacted_query,
                    "root_cause": state.lifecycle.diagnosis.confirmed_root_cause,
                    "verification": state.lifecycle.verification.summary,
                    "recommendations": state.outcome.recommendations,
                    "evidence_types": list(dict.fromkeys(item.type for item in state.evidence)),
                }
            ),
            max(state.outcome.confidence, 0.8),
            json_dump(
                {
                    "run_id": state.run_id,
                    "incident_id": state.incident_id,
                    "human_confirmed": True,
                    "recovery_verified": True,
                }
            ),
        )
        await conn.execute(
            """
            UPDATE memory_records
            SET status = 'archived', superseded_by = $2, updated_at = now()
            WHERE idempotency_key = $1 AND tier = 'candidate'
            """,
            f"candidate:{state.run_id}",
            stored_memory_id,
        )
        return str(stored_memory_id)

    async def _persist_success_experience(self, conn: Any, state: WorkflowState) -> None:
        await conn.execute(
            """
            INSERT INTO experience_records (
                id, idempotency_key, source_run_id, incident_id, outcome,
                signature, summary, root_cause, evidence_types, actions,
                applicability, confidence, status
            )
            VALUES (
                $1, $2, $3, $4, 'success', $5, $6, $7, $8::jsonb,
                $9::jsonb, $10::jsonb, $11, 'verified'
            )
            ON CONFLICT (idempotency_key) DO UPDATE SET
                summary = EXCLUDED.summary,
                root_cause = EXCLUDED.root_cause,
                evidence_types = EXCLUDED.evidence_types,
                actions = EXCLUDED.actions,
                applicability = EXCLUDED.applicability,
                confidence = EXCLUDED.confidence,
                status = 'verified',
                updated_at = now()
            """,
            new_id("exp"),
            f"success:{state.run_id}",
            state.run_id,
            state.incident_id,
            f"{state.scope.service}:{state.lifecycle.diagnosis.confirmed_root_cause}"[:500],
            state.outcome.summary or "事故恢复并关闭",
            state.lifecycle.diagnosis.confirmed_root_cause,
            json_dump(list(dict.fromkeys(item.type for item in state.evidence))),
            json_dump(
                [item.model_dump(mode="json") for item in state.lifecycle.remediation.actions]
            ),
            json_dump(
                {
                    "scope_kind": _value(state.scope.kind),
                    "service": state.scope.service,
                    "resource_id": state.scope.resource_id,
                }
            ),
            max(state.outcome.confidence, 0.8),
        )

    async def _persist_eval_sample(
        self,
        conn: Any,
        result: IncidentClosureResult,
    ) -> None:
        sample = result.eval_sample
        await conn.execute(
            """
            INSERT INTO evaluation_samples (
                id, source_run_id, incident_id, capability_id, query, expected_root_cause,
                evidence_types, verification_status, review_status,
                dataset_split, isolation_status, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9, $10,
                    'isolated', $11::jsonb)
            ON CONFLICT (source_run_id, id) DO NOTHING
            """,
            sample.id,
            sample.source_run_id,
            sample.incident_id,
            result.state.capability_id,
            sample.query,
            sample.expected_root_cause,
            json_dump(sample.evidence_types),
            _value(sample.verification_status),
            sample.review_status,
            sample.dataset_split,
            json_dump(sample.metadata),
        )

    async def _maintain_memory_records(self, conn: Any) -> None:
        """归档到期记忆；保留审计事实但禁止继续召回。"""
        await conn.execute(
            """
            UPDATE memory_records
            SET status = 'expired', updated_at = now()
            WHERE expires_at <= now()
              AND status IN ('active', 'candidate', 'verified')
            """
        )

    async def _update_profiles(
        self,
        conn: Any,
        state: WorkflowState,
        actor: str,
        memory_id: str,
    ) -> None:
        subjects = [("operator", actor)]
        if state.scope.service:
            subjects.append(("service", state.scope.service))
        elif state.scope.resource_id:
            subjects.append(("resource", state.scope.resource_id))
        for subject_type, subject_key in subjects:
            await conn.execute(
                """
                INSERT INTO entity_profiles (
                    id, subject_type, subject_key, attributes, source_memory_ids
                )
                VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)
                ON CONFLICT (subject_type, subject_key) DO UPDATE SET
                    revision = entity_profiles.revision + 1,
                    attributes = entity_profiles.attributes || EXCLUDED.attributes,
                    source_memory_ids = (
                        SELECT jsonb_agg(DISTINCT value)
                        FROM jsonb_array_elements(
                            entity_profiles.source_memory_ids || EXCLUDED.source_memory_ids
                        ) AS value
                    ),
                    updated_at = now()
                """,
                new_id("profile"),
                subject_type,
                subject_key,
                json_dump(
                    {
                        "last_incident_id": state.incident_id,
                        "last_verified_root_cause": state.lifecycle.diagnosis.confirmed_root_cause,
                        "last_closed_at": datetime.now(timezone.utc).isoformat(),
                    }
                ),
                json_dump([memory_id]),
            )

    async def _close_incident_facts(self, conn: Any, state: WorkflowState) -> None:
        if not state.incident_id:
            return
        await conn.execute(
            """
            UPDATE incidents
            SET status = 'closed', ended_at = now(), updated_at = now()
            WHERE id = $1
            """,
            state.incident_id,
        )
        await conn.execute(
            """
            UPDATE incident_groups
            SET status = 'closed', closed_at = now(), updated_at = now()
            WHERE id = (
                SELECT incident_group_id FROM incidents WHERE id = $1
            )
            """,
            state.incident_id,
        )


workflow_repository = WorkflowRepository()
