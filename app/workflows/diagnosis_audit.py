"""统一故障排查的 Postgres Agent/Evidence/Tool 审计。"""

from __future__ import annotations

from typing import Any

from app.evidence.models import EvidenceCreate
from app.evidence.repository import evidence_repository
from app.incidents.models import EvidenceSource
from app.orchestration.repository import agent_run_repository
from app.workflows.models import EvidenceItem, WorkflowState


class WorkflowDiagnosisAudit:
    """把一次自适应诊断及其 Specialist 产物写入现有事实表。"""

    def __init__(self, state: WorkflowState) -> None:
        self.state = state
        self.agent_run_id = ""
        self.input_evidence_id = ""
        self.evidence_ids: list[str] = []
        self.persisted_workflow_evidence_ids: set[str] = set()
        self.tool_call_count = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0

    @property
    def enabled(self) -> bool:
        return bool(
            self.state.revision > 0
            and self.state.incident_group_id
            and self.state.incident_id
        )

    async def start(self) -> None:
        if not self.enabled:
            return
        self.input_evidence_id = await evidence_repository.create(
            EvidenceCreate(
                incident_group_id=self.state.incident_group_id,
                incident_id=self.state.incident_id,
                source=EvidenceSource.ALERT,
                type="workflow_query",
                summary=self.state.query.rewritten_query[:1000],
                content={
                    "run_id": self.state.run_id,
                    "intent": self.state.query.primary_intent.value,
                    "scope": self.state.scope.model_dump(mode="json"),
                },
                metadata={"workflow_run_id": self.state.run_id},
            )
        )
        self.evidence_ids.append(self.input_evidence_id)
        self.agent_run_id = await agent_run_repository.create_run(
            task_id="",
            incident_group_id=self.state.incident_group_id,
            incident_id=self.state.incident_id,
            agent_name="unified_diagnosis_coordinator",
            agent_version=self.state.state_version,
            input_ref=self.input_evidence_id,
        )

    async def capture_state_evidence(self) -> list[str]:
        if not self.agent_run_id:
            return []
        created: list[str] = []
        for item in self.state.evidence:
            if item.id in self.persisted_workflow_evidence_ids:
                continue
            evidence_id = await self._persist_evidence(item)
            self.persisted_workflow_evidence_ids.add(item.id)
            self.evidence_ids.append(evidence_id)
            created.append(evidence_id)
        return created

    async def record_event(
        self,
        event: dict[str, Any],
        *,
        new_evidence_ids: list[str] | None = None,
    ) -> None:
        if not self.agent_run_id:
            return
        event_type = str(event.get("type") or "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if event_type == "tool_call":
            status = str(data.get("status") or "unknown")
            await agent_run_repository.record_tool_call(
                agent_run_id=self.agent_run_id,
                task_id="",
                incident_group_id=self.state.incident_group_id,
                tool_name=str(data.get("name") or "unknown_tool"),
                status=status,
                args={
                    "read_only": bool(data.get("read_only")),
                    "scope": self.state.scope.model_dump(mode="json"),
                },
                result_ref=(new_evidence_ids or [""])[-1],
                elapsed_ms=int(data.get("elapsed_ms") or 0),
                error="" if status == "ok" else status,
            )
            self.tool_call_count += 1
        elif event_type in {"usage", "stats"}:
            self.input_tokens += int(data.get("input_tokens") or 0)
            self.output_tokens += int(data.get("output_tokens") or 0)
            self.total_tokens += int(data.get("total_tokens") or 0)
        elif event_type == "evidence" and data.get("agent"):
            await self._record_specialist_run(
                agent_name=str(data["agent"]),
                evidence_ids=new_evidence_ids or [],
            )

    async def finish(self, *, status: str, error: str = "") -> None:
        if not self.agent_run_id:
            return
        await self.capture_state_evidence()
        await agent_run_repository.finish_run(
            self.agent_run_id,
            status=status,
            output_ref=self.evidence_ids[-1] if self.evidence_ids else "",
            evidence_ids=self.evidence_ids,
            tool_call_count=self.tool_call_count,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.total_tokens,
            error=error,
        )

    async def _persist_evidence(self, item: EvidenceItem) -> str:
        return await evidence_repository.create(
            EvidenceCreate(
                incident_group_id=self.state.incident_group_id,
                incident_id=self.state.incident_id,
                source=item.source,
                type=item.type,
                summary=item.summary,
                content=item.content,
                score=item.confidence,
                occurred_at=item.observed_at,
                metadata={
                    **item.metadata,
                    "workflow_run_id": self.state.run_id,
                    "workflow_evidence_id": item.id,
                    "status": item.status.value,
                    "tool_call_id": item.tool_call_id,
                    "scope": item.scope.model_dump(mode="json"),
                },
            )
        )

    async def _record_specialist_run(
        self,
        *,
        agent_name: str,
        evidence_ids: list[str],
    ) -> None:
        run_id = await agent_run_repository.create_run(
            task_id="",
            incident_group_id=self.state.incident_group_id,
            incident_id=self.state.incident_id,
            agent_name=agent_name,
            agent_version=self.state.state_version,
            input_ref=self.input_evidence_id,
        )
        await agent_run_repository.finish_run(
            run_id,
            status="succeeded",
            output_ref=evidence_ids[-1] if evidence_ids else "",
            evidence_ids=evidence_ids,
        )
