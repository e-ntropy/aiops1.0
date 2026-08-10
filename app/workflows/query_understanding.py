"""Query 深度理解、改写与确定性降级。"""

from __future__ import annotations

import re
from collections.abc import Sequence

from loguru import logger

from app.core.llm import get_chat_llm
from app.core.structured import ainvoke_structured
from app.runtime.agent_harness import get_agent_harness
from app.workflows.models import (
    QuerySubtask,
    QueryUnderstanding,
    RiskLevel,
    WorkflowIntent,
)

_LOCAL_TERMS = ("本机", "这台电脑", "我的电脑", "当前电脑", "localhost", "本地机器")
_LIVE_TERMS = ("现在", "当前", "查看", "检查", "运行", "进程", "端口", "占用")
_DIAGNOSIS_TERMS = ("故障", "报错", "异常", "失败", "超时", "oom", "根因", "排查")
_OPTIMIZATION_TERMS = ("优化", "调优", "降低占用", "提升性能", "怎么改", "清理")
_INSPECTION_TERMS = ("巡检", "健康检查", "全面检查", "有没有问题")
_REVIEW_TERMS = ("复盘", "上次事故", "事故总结", "时间线")
_EVAL_TERMS = ("评测", "评估模型", "benchmark", "回归测试")
_KNOWLEDGE_TERMS = ("是什么", "为什么", "原理", "如何理解", "区别", "什么意思")
_WRITE_TERMS = ("重启", "停止", "结束进程", "删除", "修改配置", "扩容", "回滚", "执行")
_REMOTE_HINT = re.compile(
    r"\b(?:[a-zA-Z][a-zA-Z0-9_.-]{2,}|(?:\d{1,3}\.){3}\d{1,3})\b"
)


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(term in lowered for term in terms)


def deterministic_understanding(raw_query: str) -> QueryUnderstanding:
    """不依赖 LLM 的安全兜底；宁可追问，也不擅自绑定远程资源。"""
    text = " ".join(raw_query.strip().split())
    if not text:
        raise ValueError("query 不能为空")

    intents: list[WorkflowIntent] = []
    if _contains_any(text, _EVAL_TERMS):
        intents.append(WorkflowIntent.EVALUATION)
    if _contains_any(text, _REVIEW_TERMS):
        intents.append(WorkflowIntent.INCIDENT_REVIEW)
    if _contains_any(text, _INSPECTION_TERMS):
        intents.append(WorkflowIntent.SYSTEM_INSPECTION)
    if _contains_any(text, _DIAGNOSIS_TERMS):
        intents.append(WorkflowIntent.FAULT_DIAGNOSIS)
    if _contains_any(text, _OPTIMIZATION_TERMS):
        intents.append(WorkflowIntent.OPTIMIZATION)
    if _contains_any(text, _LIVE_TERMS):
        intents.append(WorkflowIntent.STATUS_QUERY)
    if _contains_any(text, _KNOWLEDGE_TERMS) or not intents:
        intents.append(WorkflowIntent.KNOWLEDGE_QA)
    intents = list(dict.fromkeys(intents))

    priority = (
        WorkflowIntent.SYSTEM_INSPECTION,
        WorkflowIntent.FAULT_DIAGNOSIS,
        WorkflowIntent.OPTIMIZATION,
        WorkflowIntent.STATUS_QUERY,
        WorkflowIntent.INCIDENT_REVIEW,
        WorkflowIntent.EVALUATION,
        WorkflowIntent.KNOWLEDGE_QA,
    )
    primary = next(intent for intent in priority if intent in intents)
    is_local = _contains_any(text, _LOCAL_TERMS)
    needs_live_data = primary in {
        WorkflowIntent.STATUS_QUERY,
        WorkflowIntent.SYSTEM_INSPECTION,
        WorkflowIntent.FAULT_DIAGNOSIS,
        WorkflowIntent.OPTIMIZATION,
    }
    remote_tokens = [token for token in _REMOTE_HINT.findall(text) if "." in token]
    missing: list[str] = []
    if needs_live_data and not is_local and not remote_tokens:
        missing.append("目标环境或资源")

    write_requested = _contains_any(text, _WRITE_TERMS)
    requires_confirmation = bool(missing or write_requested)
    if missing:
        clarification = "请确认要查询的是本机、测试环境还是生产环境，并提供目标主机、服务或实例。"
    elif write_requested:
        clarification = "请求可能包含写操作。是否先只进行只读分析，并在执行任何变更前再次确认？"
    else:
        clarification = ""

    goals: list[str] = []
    if primary == WorkflowIntent.KNOWLEDGE_QA:
        goals = ["检索可信运维知识", "基于来源回答问题"]
    elif primary == WorkflowIntent.STATUS_QUERY:
        goals = ["读取目标当前状态", "解释关键指标"]
    elif primary == WorkflowIntent.SYSTEM_INSPECTION:
        goals = ["采集系统健康快照", "识别异常项", "给出只读建议"]
    elif primary == WorkflowIntent.FAULT_DIAGNOSIS:
        goals = ["确认故障作用域", "采集最小证据", "判断根因"]
    elif primary == WorkflowIntent.OPTIMIZATION:
        goals = ["建立当前资源基线", "识别优化机会", "生成带风险的优化计划"]
    elif primary == WorkflowIntent.INCIDENT_REVIEW:
        goals = ["还原事故时间线", "总结根因与行动项"]
    else:
        goals = ["选择评测范围", "执行可复现评估"]

    subtasks = [
        QuerySubtask(
            objective=goal,
            requires_live_data=needs_live_data and index == 0,
            risk_level=RiskLevel.READ_ONLY,
        )
        for index, goal in enumerate(goals)
    ]
    risk = RiskLevel.HIGH if write_requested else RiskLevel.READ_ONLY
    return QueryUnderstanding(
        raw_query=raw_query,
        rewritten_query=f"{primary.value}: {text}",
        primary_intent=primary,
        secondary_intents=[intent for intent in intents if intent != primary],
        goals=goals,
        subtasks=subtasks,
        entities={"environment": "local"} if is_local else {},
        missing_information=missing,
        risk_level=risk,
        confidence=0.72 if not missing else 0.48,
        requires_confirmation=requires_confirmation,
        clarification_question=clarification,
        via="deterministic",
    )


_SYSTEM_PROMPT = """你是 AIOps 请求理解器。把用户请求改写为可执行但不扩大授权的任务。
硬约束：
1. raw_query 必须逐字保留用户原始输入；rewritten_query 只能澄清语义，不能新增写操作；
2. 区分知识问答、实时状态、系统巡检、故障诊断、优化、复盘、评测；
3. 现场查询必须确定 environment/resource/time range；缺失时 requires_confirmation=true；
4. 涉及重启、停止、删除、修改、扩容、回滚时 risk_level=high 并要求确认；
5. 把目标拆成 1-8 个明确 subtasks；只输出符合 schema 的 json。"""


async def understand_query(raw_query: str, *, use_llm: bool = True) -> QueryUnderstanding:
    fallback = deterministic_understanding(raw_query)
    if not use_llm:
        return fallback
    try:
        harness = get_agent_harness()
        model = harness.router_model()
        llm = get_chat_llm(model=model, temperature=0, timeout=25, max_retries=1)
        understood = await ainvoke_structured(
            llm=llm,
            schema_cls=QueryUnderstanding,
            model_name=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": raw_query},
            ],
        )
        # 代码层硬墙：LLM 不得改写原始输入，也不得把高风险降成只读。
        understood.raw_query = raw_query
        if fallback.risk_level == RiskLevel.HIGH:
            understood.risk_level = RiskLevel.HIGH
            understood.requires_confirmation = True
            understood.clarification_question = (
                understood.clarification_question
                or fallback.clarification_question
            )
        understood.via = "llm"
        return understood
    except Exception as exc:  # noqa: BLE001 - Query 理解必须 fail-safe 降级
        logger.warning(
            "[workflow] query understanding 降级: {}: {}",
            type(exc).__name__,
            exc,
        )
        return fallback
