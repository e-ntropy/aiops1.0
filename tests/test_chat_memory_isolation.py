from __future__ import annotations

import unittest
from unittest.mock import patch

from app.services import chat_memory


class _FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = {}

    async def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        self.lists[key] = self.lists.get(key, [])[start : end + 1]

    async def expire(self, key: str, ttl: int) -> None:
        return None

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        return self.lists.get(key, [])[start : end + 1]


class ChatMemoryIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_diagnosis_reports_are_isolated_by_session(self) -> None:
        redis = _FakeRedis()
        with patch("app.services.chat_memory._get_redis", return_value=redis):
            await chat_memory.append_diagnosis_report("A 的诊断", session_id="session-a")
            await chat_memory.append_diagnosis_report("B 的诊断", session_id="session-b")
            reports_a = await chat_memory.get_recent_diagnosis_reports(
                session_id="session-a"
            )
            reports_b = await chat_memory.get_recent_diagnosis_reports(
                session_id="session-b"
            )

        self.assertEqual([item["report"] for item in reports_a], ["A 的诊断"])
        self.assertEqual([item["report"] for item in reports_b], ["B 的诊断"])
        self.assertNotEqual(
            chat_memory._diagnosis_reports_key("session-a"),
            chat_memory._diagnosis_reports_key("session-b"),
        )

    async def test_missing_session_never_writes_global_report(self) -> None:
        redis = _FakeRedis()
        with patch("app.services.chat_memory._get_redis", return_value=redis):
            await chat_memory.append_diagnosis_report("不得共享", session_id=None)
        self.assertEqual(redis.lists, {})


if __name__ == "__main__":
    unittest.main()
