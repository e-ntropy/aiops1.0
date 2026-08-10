"""从 QueryUnderstanding 构造并校验 TargetScope。"""

from __future__ import annotations

import ipaddress
import re

from app.workflows.models import (
    QueryUnderstanding,
    ScopeKind,
    TargetScope,
    WorkflowIntent,
)

_IP_OR_HOST = re.compile(
    r"\b((?:\d{1,3}\.){3}\d{1,3}|[a-zA-Z][a-zA-Z0-9-]*(?:\.[a-zA-Z0-9-]+)+)\b"
)
_LOCAL_TERMS = ("本机", "这台电脑", "我的电脑", "当前电脑", "localhost", "本地机器")


def intent_requires_scope(intent: WorkflowIntent) -> bool:
    return intent in {
        WorkflowIntent.STATUS_QUERY,
        WorkflowIntent.SYSTEM_INSPECTION,
        WorkflowIntent.FAULT_DIAGNOSIS,
        WorkflowIntent.OPTIMIZATION,
        WorkflowIntent.CAPACITY_PERFORMANCE,
    }


def resolve_scope(understanding: QueryUnderstanding) -> TargetScope:
    raw = understanding.raw_query
    lowered = raw.lower()
    if not intent_requires_scope(understanding.primary_intent):
        return TargetScope(kind=ScopeKind.NONE, validated=True, source="not_required")

    if any(term in lowered for term in _LOCAL_TERMS):
        return TargetScope(
            kind=ScopeKind.LOCAL_HOST,
            environment="local",
            resource_id="localhost",
            validated=True,
            source="query_explicit_local",
        )

    entity_environment = understanding.entities.get("environment", "").strip()
    entity_resource = understanding.entities.get("resource_id", "").strip()
    entity_service = understanding.entities.get("service", "").strip()
    target_match = _IP_OR_HOST.search(raw)
    resource = entity_resource or (target_match.group(1) if target_match else "")
    if resource and re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", resource):
        try:
            ipaddress.ip_address(resource)
        except ValueError:
            resource = ""
    if resource or entity_service:
        return TargetScope(
            kind=ScopeKind.SERVICE if entity_service else ScopeKind.REMOTE_HOST,
            environment=entity_environment or "remote",
            resource_id=resource,
            service=entity_service,
            validated=True,
            source="query_entity",
        )

    return TargetScope(
        kind=ScopeKind.NONE,
        environment=entity_environment,
        validated=False,
        validation_errors=["实时操作需要明确目标环境和资源，禁止默认读取本机"],
        source="unresolved",
    )
