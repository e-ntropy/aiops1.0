"""可复现的 Memory 召回过滤与排序策略。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _active(record: dict[str, Any], *, now: datetime) -> bool:
    if record.get("superseded_by"):
        return False
    expires_at = _time(record.get("expires_at"))
    return expires_at is None or expires_at > now


def is_governed_memory_match(
    record: dict[str, Any],
    *,
    session_id: str,
    incident_id: str = "",
    service: str = "",
    scope_key: str = "",
    now: datetime | None = None,
) -> bool:
    """判断一条记录能否进入当前工作流上下文。"""

    now = now or datetime.now(timezone.utc)
    if not _active(record, now=now):
        return False
    tier = str(record.get("tier") or "")
    status = str(record.get("status") or "")
    if tier == "session":
        return status == "active" and str(record.get("session_id") or "") == session_id
    if tier == "incident":
        return bool(incident_id) and status in {"active", "verified"} and str(
            record.get("incident_id") or ""
        ) == incident_id
    if tier != "verified_knowledge":
        return False
    if status != "verified" or record.get("redaction_passed") is not True:
        return False
    record_service = str(record.get("service") or "")
    record_scope = str(record.get("scope_key") or "")
    if service:
        if record_service not in {"", service}:
            return False
    elif record_service:
        return False
    if scope_key:
        return record_scope in {"", scope_key}
    return not record_scope


def select_governed_memories(
    records: list[dict[str, Any]],
    *,
    session_id: str,
    incident_id: str = "",
    service: str = "",
    scope_key: str = "",
    limit: int = 20,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    selected = [
        item
        for item in records
        if is_governed_memory_match(
            item,
            session_id=session_id,
            incident_id=incident_id,
            service=service,
            scope_key=scope_key,
            now=now,
        )
    ]
    tier_rank = {"incident": 0, "verified_knowledge": 1, "session": 2}
    selected.sort(
        key=lambda item: (
            tier_rank.get(str(item.get("tier") or ""), 9),
            -float(item.get("confidence") or 0),
            str(item.get("id") or ""),
        )
    )
    return selected[: max(0, limit)]


def select_flat_memories(
    records: list[dict[str, Any]],
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """消融基线：不区分状态、租户作用域和生命周期，仅按置信度取 Top-K。"""

    return sorted(
        records,
        key=lambda item: (-float(item.get("confidence") or 0), str(item.get("id") or "")),
    )[: max(0, limit)]
