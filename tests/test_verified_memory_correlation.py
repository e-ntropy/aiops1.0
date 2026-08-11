from __future__ import annotations

import unittest

from app.diagnosis_graphs.deep_diagnosis_graph import correlation_context_node


class VerifiedMemoryCorrelationTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_verified_postgres_memory_becomes_evidence(self) -> None:
        result = await correlation_context_node(
            {
                "input": "Redis OOM",
                "recalled_memories": [
                    {
                        "id": "mem_verified",
                        "tier": "verified_knowledge",
                        "status": "verified",
                        "content": {
                            "root_cause": "maxmemory 设置过低",
                            "verification": "调整后恢复",
                        },
                    },
                    {
                        "id": "mem_candidate",
                        "tier": "candidate",
                        "status": "candidate",
                        "content": {"root_cause": "未经确认的猜测"},
                    },
                ],
            }
        )

        evidences = result["evidences"]
        self.assertEqual(len(evidences), 1)
        self.assertEqual(evidences[0]["type"], "verified_memory")
        self.assertEqual(evidences[0]["content"]["memory_id"], "mem_verified")
        self.assertNotIn("未经确认", evidences[0]["summary"])


if __name__ == "__main__":
    unittest.main()
