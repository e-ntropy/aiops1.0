from __future__ import annotations

import unittest

from app.skills.registry import reload_skill_registry
from app.tools.meta import get_meta


class SkillAssetTests(unittest.TestCase):
    def test_builtin_specialist_skills_load_with_readonly_tools(self) -> None:
        registry = reload_skill_registry()
        expected = {
            "host_resource_diagnosis",
            "network_diagnosis",
            "container_diagnosis",
            "database_middleware_diagnosis",
            "application_runtime_diagnosis",
            "message_queue_diagnosis",
            "generic_oncall",
        }
        self.assertTrue(expected.issubset(set(registry.names())))
        for name in expected:
            skill = registry.get(name)
            self.assertIsNotNone(skill)
            self.assertTrue(skill.playbook.strip())
            self.assertTrue(
                all(get_meta(tool).effective_read_only({}) for tool in skill.allowed_tools),
                msg=f"{name} contains a non-read-only or unregistered tool",
            )


if __name__ == "__main__":
    unittest.main()
