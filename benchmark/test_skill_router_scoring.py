from __future__ import annotations

import unittest

from app.runtime.transitions import ROUTER_LLM_FAILED, ROUTER_OK, ROUTER_OUT_OF_SCOPE
from benchmark.run_skill_router_benchmark import (
    ERROR_OUTCOME,
    OUT_OF_SCOPE,
    classify_router_outcome,
    score_router_result,
)


class SkillRouterScoringTests(unittest.TestCase):
    def test_explicit_oos_transition_is_oos(self) -> None:
        result = {
            "selected_skill": "generic_oncall",
            "response": "# 无法启动 OnCall 诊断",
            "transition_history": [{"reason": ROUTER_OUT_OF_SCOPE}],
        }
        self.assertEqual(classify_router_outcome(result), OUT_OF_SCOPE)
        self.assertEqual(score_router_result(OUT_OF_SCOPE, result), (True, OUT_OF_SCOPE))

    def test_arbitrary_response_is_not_oos(self) -> None:
        result = {
            "selected_skill": "generic_oncall",
            "response": "普通提前结束报告",
            "transition_history": [{"reason": ROUTER_OK}],
        }
        self.assertEqual(classify_router_outcome(result), "generic_oncall")
        self.assertEqual(
            score_router_result(OUT_OF_SCOPE, result),
            (False, "generic_oncall"),
        )

    def test_rule_fallback_oos_requires_all_signals(self) -> None:
        result = {
            "selected_skill": "generic_oncall",
            "skill_reason": "Router LLM 调用失败后, 规则兜底判断为非 OnCall 输入",
            "plan": [],
            "response": "# 无法启动 OnCall 诊断",
            "transition_history": [{"reason": ROUTER_LLM_FAILED}],
        }
        self.assertEqual(classify_router_outcome(result), OUT_OF_SCOPE)

    def test_llm_failure_with_oncall_fallback_is_not_oos(self) -> None:
        result = {
            "selected_skill": "generic_oncall",
            "skill_reason": "Router LLM 调用失败后, 规则兜底放行到 generic_oncall",
            "transition_history": [{"reason": ROUTER_LLM_FAILED}],
        }
        self.assertEqual(classify_router_outcome(result), "generic_oncall")

    def test_benchmark_error_is_counted_as_error(self) -> None:
        result = {"__benchmark_error__": "TimeoutError"}
        self.assertEqual(classify_router_outcome(result), ERROR_OUTCOME)
        self.assertEqual(
            score_router_result("generic_oncall", result),
            (False, ERROR_OUTCOME),
        )


if __name__ == "__main__":
    unittest.main()
