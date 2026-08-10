"""Human-in-the-Loop (HITL) 诊断中断模块.

在 LangGraph 诊断图中增加人工介入节点, 支持诊断流程中暂停等待人工确认。

使用场景:
  1. 高危操作前的人确认 (与 ASK_DESTRUCTIVE 模式协同)
  2. 诊断结论不确定时请求人工判断
  3. 多轮交互: 人工补充信息后继续诊断

实现方式:
  - LangGraph interrupt() 机制: 在关键节点前暂停, 等待外部输入
  - Postgres approval_requests 表作为持久化审批队列
  - SSE 推送 interrupt 事件到前端, 前端展示确认卡片

设计原则 (来自 LangGraph 官方 Human-in-the-loop 模式):
  - interrupt() 会抛出 GraphInterrupt, 图暂停在当前状态
  - 外部调用 graph.ainvoke(Command(resume=...), config) 继续执行
  - 支持 resume 时传入额外信息 (如人工备注、修正参数)

参考:
  - LangGraph docs: https://langchain-ai.github.io/langgraph/how-tos/human_in_the_loop/
  - 本项目现有审批: app/runtime/approvals.py (ASK_DESTRUCTIVE 模式的 Postgres 审批)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from loguru import logger

from app.runtime.transitions import make_transition


# ---- Interrupt 类型 ----
class InterruptType(str, Enum):
    """人工介入类型."""
    CONFIRM_DESTRUCTIVE = "confirm_destructive"   # 确认写操作
    CONFIRM_DIAGNOSIS = "confirm_diagnosis"        # 确认诊断结论
    REQUEST_INFO = "request_info"                  # 请求补充信息
    CONFIRM_REMEDIATION = "confirm_remediation"    # 确认处置建议


@dataclass
class InterruptRequest:
    """人工介入请求."""
    type: InterruptType
    title: str
    message: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    context: dict[str, Any] = field(default_factory=dict)
    options: list[str] = field(default_factory=list)
    timeout_sec: int = 300
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "interrupt_id": self.id,
            "type": str(self.type),
            "title": self.title,
            "message": self.message,
            "context": self.context,
            "options": self.options,
            "timeout_sec": self.timeout_sec,
            "created_at": self.created_at,
        }


# ---- LangGraph interrupt 包装 ----
def create_interrupt(request: InterruptRequest) -> dict[str, Any]:
    """创建 LangGraph interrupt 事件.

    在 LangGraph 节点中调用此函数会暂停图执行,
    等待外部输入 resume。在非 LangGraph 环境 (如测试) 中,
    返回一个标记了 needs_human 的 state patch。

    使用方式:
        # 在 graph node 中:
        interrupt_req = InterruptRequest(
            type=InterruptType.CONFIRM_DESTRUCTIVE,
            title="确认重启 Docker 容器?",
            message="Agent 建议重启容器 nginx-prod。此操作会导致短暂服务中断。",
            options=["确认重启", "取消", "改为仅查看日志"],
        )
        result = create_interrupt(interrupt_req)
        # 图暂停, 等待人工输入
        # result 包含人工的选择和备注
    """
    try:
        from langgraph.types import interrupt as lg_interrupt

        # LangGraph interrupt: 暂停图, 等待 resume
        approved = lg_interrupt(request.to_dict())
        return {
            "interrupt_resolved": True,
            "interrupt_id": request.id,
            "human_decision": approved,
            "transition_history": [
                make_transition(
                    "human_interrupt", "interrupt_resolved",
                    f"type={request.type} id={request.id} decision={str(approved)[:100]}",
                )
            ],
        }
    except (ImportError, RuntimeError):
        # 非 LangGraph 环境 (测试/直接调用): 返回 pending 标记
        logger.info(f"[HITL] LangGraph interrupt not available (test/non-graph mode), returning pending state")
        return {
            "interrupt_pending": True,
            "interrupt_id": request.id,
            "interrupt_request": request.to_dict(),
            "transition_history": [
                make_transition(
                    "human_interrupt", "interrupt_pending",
                    f"type={request.type} id={request.id} (non-graph mode)",
                )
            ],
        }


# ---- HITL 诊断节点 ----
async def hitl_remediation_gate(state: dict[str, Any]) -> dict[str, Any]:
    """处置建议前的人确认门禁.

    在 RemediationPlanner 之后、生成最终报告之前插入此节点。
    如果处置建议包含写操作, 暂停等人工确认。

    集成方式 (在 deep_diagnosis_graph.py 中):
        wf.add_node("hitl_gate", hitl_remediation_gate)
        wf.add_edge("remediation_planner", "hitl_gate")
        wf.add_conditional_edges("hitl_gate", _after_hitl, {
            "continue": "report",
            "interrupt": END,  # 暂停, 等人工 resume
        })
    """
    remediation = state.get("remediation") or {}
    steps = remediation.get("steps") or []
    requires_confirm = remediation.get("requires_human_confirm", False)

    # 检查是否有写操作步骤
    write_steps = [s for s in steps if "写操作" in s or "需人工" in s]
    if not write_steps and not requires_confirm:
        logger.info("[HITL] 无写操作步骤, 跳过人工确认")
        return {
            "hitl_status": "skipped",
            "hitl_reason": "no destructive steps",
            "transition_history": [
                make_transition("hitl_gate", "hitl_skipped", "no destructive steps")
            ],
        }

    # 创建中断请求
    steps_text = "\n".join(f"- {s}" for s in write_steps)
    interrupt_req = InterruptRequest(
        type=InterruptType.CONFIRM_REMEDIATION,
        title="⚠️ 处置建议包含写操作, 需要人工确认",
        message=(
            f"Agent 建议以下写操作:\n\n{steps_text}\n\n"
            f"请确认是否执行, 或选择替代方案。"
        ),
        context={
            "rca_root_cause": str(state.get("rca", {}).get("root_cause", "")),
            "remediation_steps": steps,
            "write_steps": write_steps,
        },
        options=["确认全部执行", "仅执行只读步骤", "全部拒绝, 生成报告"],
        timeout_sec=300,
    )

    result = create_interrupt(interrupt_req)

    if result.get("interrupt_pending"):
        # 非 LangGraph 模式: 自动降级为跳过写入
        logger.warning("[HITL] 非 LangGraph 模式, 自动跳过写操作")
        return {
            "hitl_status": "auto_skipped",
            "hitl_reason": "non-graph mode, write ops skipped",
            "remediation": {
                **remediation,
                "steps": [s for s in steps if s not in write_steps],
                "skipped_write_steps": write_steps,
            },
            "transition_history": [
                make_transition("hitl_gate", "hitl_auto_skip", f"skipped {len(write_steps)} write ops")
            ],
        }

    # LangGraph 模式: 根据人工决策处理
    decision = str(result.get("human_decision", ""))
    logger.info(f"[HITL] 人工决策: {decision[:100]}")

    if "全部执行" in decision or "确认" in decision:
        return {
            "hitl_status": "approved",
            "hitl_reason": f"human approved: {decision[:80]}",
            "transition_history": [
                make_transition("hitl_gate", "hitl_approved", decision[:80])
            ],
        }
    elif "仅执行只读" in decision:
        readonly_steps = [s for s in steps if "只读" in s]
        return {
            "hitl_status": "partial",
            "hitl_reason": "human chose read-only only",
            "remediation": {
                **remediation,
                "steps": readonly_steps,
                "skipped_write_steps": write_steps,
            },
            "transition_history": [
                make_transition("hitl_gate", "hitl_partial", f"executing {len(readonly_steps)} readonly steps")
            ],
        }
    else:
        # 拒绝 → 跳过写入步骤
        return {
            "hitl_status": "rejected",
            "hitl_reason": "human rejected write ops",
            "remediation": {
                **remediation,
                "steps": [s for s in steps if s not in write_steps],
                "skipped_write_steps": write_steps,
            },
            "transition_history": [
                make_transition("hitl_gate", "hitl_rejected", f"rejected {len(write_steps)} write ops")
            ],
        }


# ---- HITL 诊断确认节点 ----
async def hitl_diagnosis_confirm(state: dict[str, Any]) -> dict[str, Any]:
    """诊断结论置信度低时, 请求人工确认.

    当 RCA Judge 的 confidence < 阈值时触发。
    """
    rca = state.get("rca") or {}
    confidence = float(rca.get("confidence", 0))
    threshold = 0.5  # 低于此阈值触发人工确认

    if confidence >= threshold:
        return {
            "hitl_diag_status": "skipped",
            "hitl_diag_reason": f"confidence {confidence:.2f} >= {threshold}",
            "transition_history": [
                make_transition("hitl_diag", "hitl_diag_skipped", f"conf={confidence:.2f}>={threshold}")
            ],
        }

    interrupt_req = InterruptRequest(
        type=InterruptType.CONFIRM_DIAGNOSIS,
        title="⚠️ 诊断置信度较低, 请人工判断",
        message=(
            f"Agent 判定根因: {rca.get('root_cause', '未知')}\n"
            f"置信度: {confidence:.2f} (低于阈值 {threshold})\n\n"
            f"判定理由: {rca.get('reasoning', '无')}\n\n"
            f"请确认此结论是否合理, 或提供补充信息。"
        ),
        context={"rca": rca, "candidates": state.get("candidates", [])},
        options=["结论合理, 继续", "结论有误, 请重新分析", "补充信息..."],
        timeout_sec=300,
    )

    result = create_interrupt(interrupt_req)

    if result.get("interrupt_pending"):
        return {
            "hitl_diag_status": "auto_continued",
            "hitl_diag_reason": "non-graph mode, auto continue",
            "transition_history": [
                make_transition("hitl_diag", "hitl_diag_auto", f"conf={confidence:.2f}")
            ],
        }

    decision = str(result.get("human_decision", ""))
    if "重新分析" in decision:
        # 清空 RCA, 触发重新判定 (或回到 EvidencePlan)
        return {
            "hitl_diag_status": "reanalyze",
            "rca": None,
            "hitl_feedback": decision,
            "transition_history": [
                make_transition("hitl_diag", "hitl_diag_reanalyze", "human requested re-analysis")
            ],
        }

    return {
        "hitl_diag_status": "confirmed",
        "hitl_feedback": decision,
        "transition_history": [
            make_transition("hitl_diag", "hitl_diag_confirmed", decision[:80])
        ],
    }


# ---- HITL Benchmark / Test ----
@dataclass
class HitlTestScenario:
    """HITL 测试场景."""
    name: str
    state: dict[str, Any]
    expected_interrupt: bool
    expected_type: InterruptType | None = None


def get_hitl_test_scenarios() -> list[HitlTestScenario]:
    """获取 HITL 测试场景集, 用于验证 HITL 逻辑正确性."""
    return [
        HitlTestScenario(
            name="包含写操作步骤 → 应触发 interrupt",
            state={
                "remediation": {
                    "steps": ["[只读] 查看日志", "[写操作·需人工] 重启容器"],
                    "requires_human_confirm": True,
                },
                "rca": {"root_cause": "容器内存泄漏", "confidence": 0.8},
            },
            expected_interrupt=True,
            expected_type=InterruptType.CONFIRM_REMEDIATION,
        ),
        HitlTestScenario(
            name="仅只读步骤 → 不触发 interrupt",
            state={
                "remediation": {
                    "steps": ["[只读] 查看CPU", "[只读] 查看内存"],
                    "requires_human_confirm": False,
                },
                "rca": {"root_cause": "CPU使用率高", "confidence": 0.9},
            },
            expected_interrupt=False,
        ),
        HitlTestScenario(
            name="低置信度诊断 → 应触发诊断确认 interrupt",
            state={
                "rca": {"root_cause": "可能是网络问题", "confidence": 0.3, "reasoning": "证据不足"},
                "candidates": [{"candidate": "网络延迟", "support_score": 0.5}],
            },
            expected_interrupt=True,
            expected_type=InterruptType.CONFIRM_DIAGNOSIS,
        ),
        HitlTestScenario(
            name="高置信度诊断 → 不触发 interrupt",
            state={
                "rca": {"root_cause": "OOM Kill", "confidence": 0.92, "reasoning": "内存超限"},
                "candidates": [{"candidate": "内存不足", "support_score": 0.95}],
            },
            expected_interrupt=False,
        ),
        HitlTestScenario(
            name="空 remediation → 不触发 interrupt",
            state={
                "remediation": {},
                "rca": {"root_cause": "未知", "confidence": 0.5},
            },
            expected_interrupt=False,
        ),
    ]


async def run_hitl_tests() -> dict[str, Any]:
    """运行 HITL 逻辑测试 (不需要 LLM).

    验证 HITL 节点的中断逻辑是否正确触发/跳过。
    因为在非 LangGraph 环境中运行, 所有 interrupt 会自动降级为 auto_skip。
    这里主要验证:
      1. 写操作检测正确
      2. 置信度阈值判断正确
      3. 边界 case 处理正确
    """
    results = []
    for scenario in get_hitl_test_scenarios():
        logger.info(f"[HITL Test] {scenario.name}")

        # 测试 remediation gate
        gate_result = await hitl_remediation_gate(scenario.state)
        gate_interrupted = gate_result.get("hitl_status") not in ("skipped", None)

        # 测试 diagnosis confirm
        diag_result = await hitl_diagnosis_confirm(scenario.state)
        diag_interrupted = diag_result.get("hitl_diag_status") not in ("skipped", None)

        # 根据场景类型判断应该由哪个 handler 触发
        if scenario.expected_type == InterruptType.CONFIRM_REMEDIATION:
            actual_interrupted = gate_interrupted
        elif scenario.expected_type == InterruptType.CONFIRM_DIAGNOSIS:
            actual_interrupted = diag_interrupted
        else:
            actual_interrupted = gate_interrupted or diag_interrupted

        passed = actual_interrupted == scenario.expected_interrupt
        results.append({
            "scenario": scenario.name,
            "expected_interrupt": scenario.expected_interrupt,
            "actual_interrupt": actual_interrupted,
            "passed": passed,
            "gate_status": gate_result.get("hitl_status"),
            "diag_status": diag_result.get("hitl_diag_status"),
        })
        status = "PASS" if passed else "FAIL"
        logger.info(f"[HITL Test] {status}: {scenario.name} (expected={scenario.expected_interrupt} actual={actual_interrupted})")

    passed = sum(1 for r in results if r["passed"])
    total = len(results)
    logger.info(f"[HITL Test] Summary: {passed}/{total} passed")
    return {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "accuracy": passed / total if total > 0 else 0,
        "details": results,
    }
