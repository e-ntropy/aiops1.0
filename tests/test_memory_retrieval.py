from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.workflows.memory_retrieval import (
    is_governed_memory_match,
    select_governed_memories,
)


class MemoryRetrievalPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 8, 11, tzinfo=timezone.utc)
        self.context = {
            "session_id": "session-a",
            "incident_id": "incident-a",
            "service": "checkout",
            "scope_key": "service|prod|checkout|node-a",
            "now": self.now,
        }

    def _record(self, **changes):
        record = {
            "id": "memory-a",
            "tier": "verified_knowledge",
            "status": "verified",
            "confidence": 0.8,
            "session_id": "",
            "incident_id": "",
            "service": "checkout",
            "scope_key": "service|prod|checkout|node-a",
            "redaction_passed": True,
            "expires_at": None,
            "superseded_by": "",
        }
        record.update(changes)
        return record

    def test_verified_memory_requires_redaction(self) -> None:
        record = self._record(redaction_passed=False)
        self.assertFalse(is_governed_memory_match(record, **self.context))

    def test_candidate_memory_is_never_recalled(self) -> None:
        record = self._record(tier="candidate", status="candidate")
        self.assertFalse(is_governed_memory_match(record, **self.context))

    def test_foreign_session_memory_is_rejected(self) -> None:
        record = self._record(tier="session", status="active", session_id="session-b")
        self.assertFalse(is_governed_memory_match(record, **self.context))

    def test_current_session_memory_is_recalled(self) -> None:
        record = self._record(tier="session", status="active", session_id="session-a")
        self.assertTrue(is_governed_memory_match(record, **self.context))

    def test_expired_and_superseded_memories_are_rejected(self) -> None:
        expired = self._record(expires_at=self.now - timedelta(seconds=1))
        superseded = self._record(superseded_by="memory-b")
        self.assertFalse(is_governed_memory_match(expired, **self.context))
        self.assertFalse(is_governed_memory_match(superseded, **self.context))

    def test_foreign_service_and_scope_are_rejected(self) -> None:
        foreign_service = self._record(service="payments")
        foreign_scope = self._record(scope_key="service|prod|checkout|node-b")
        self.assertFalse(is_governed_memory_match(foreign_service, **self.context))
        self.assertFalse(is_governed_memory_match(foreign_scope, **self.context))

    def test_incident_precedes_verified_and_session_memory(self) -> None:
        records = [
            self._record(id="session", tier="session", status="active", session_id="session-a"),
            self._record(id="verified"),
            self._record(id="incident", tier="incident", status="active", incident_id="incident-a"),
        ]
        selected = select_governed_memories(records, **self.context)
        self.assertEqual([item["id"] for item in selected], ["incident", "verified", "session"])


if __name__ == "__main__":
    unittest.main()
