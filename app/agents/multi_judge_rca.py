"""Multi-Judge 投票 RCA 判定模块.

在原有单 Judge RCAJudge 基础上, 增加 K 个独立 Judge 并行判定, 通过投票机制
提升根因判定的置信度和可靠性。

设计要点:
  - K 个 Judge 并行调用 LLM (不同 temperature 引入多样性)
  - 每个 Judge 返回 root_cause + confidence + reasoning
  - 投票规则: root_cause 文本相似度 > 0.6 视为"同意票"
  - 最终 root_cause = 得票最多的候选; confidence = 平均置信度 * 一致性因子
  - LLM 失败时降级到确定性回退 (与单 Judge 一致)

使用方式:
  from app.agents.multi_judge_rca import multi_judge_rca_node
  # 在 deep graph 中替换 rca_judge_node

与单 Judge 对比:
  - 优点: 降低单一 LLM 偏差, 置信度更可靠, 可量化"一致性"
  - 成本: LLM 调用量 = K 倍 (默认 K=3, 可通过 settings 调整)
  - 延迟: 并行调用, 延迟 ≈ max(single_judge_latency)
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from difflib import SequenceMatcher
from typing import Any

from loguru import logger

from app.config import settings
from app.incidents.models import EvidenceSource
from app.runtime.transitions import DEEP_RCA_JUDGED, make_transition

# ---- 从原 RCAJudge 复用的工具函数 ----
_RCA_SYSTEM_PROMPT = (
    "你是 SRE 根因判定法官 (RCA Judge)。下面给你一组**候选根因** (已按确定性算法初排序) 和"
    "一组**关键证据 summary** (来自多个专业 Agent 的观察结论)。\n"
    "你的职责: ① 对候选**重新排序**, 把最可能的根因排第一; ② 写一段≤200 字的中文判定理由;"
    "③ 列出最关键的 3-5 个支持证据 (按 evidence_id, 取 evidence_ids 字段里的引用)。\n\n"
    "硬性约束:\n"
    "1. **只看本 prompt 给的 summary, 不要假设你看过原始日志/指标/调用链**;\n"
    "2. 优先看 metric 类证据 (现场实测), 次看 infra (运行环境/依赖), 再看 log/runbook 和 incident_history;\n"
    "3. 如果有标记 error 的证据, 说明对应 Agent 失败, 在 reasoning 里点明这部分信息缺失;\n"
    "4. 只输出一个 JSON 对象, 不要任何解释或 markdown 围栏。\n"
    "{\n"
    ' "root_cause": "<一句话最可能根因>",\n'
    ' "ranked_candidates": ["<按可能性降序的 candidate 文本列表>"],\n'
    ' "supporting_evidence_ids": ["ev_X", ...],\n'
    ' "reasoning": "<判定理由 (中文, ≤200 字)>",\n'
    ' "confidence": <0.0-1.0>\n'
    "}"
)

_JUDGE_VARIATIONS = [
    # Judge 0: default (balanced)
    _RCA_SYSTEM_PROMPT,
    # Judge 1: metric-first bias
    _RCA_SYSTEM_PROMPT.replace(
        "优先看 metric 类证据 (现场实测), 次看 infra (运行环境/依赖), 再看 log/runbook",
        "**极度优先** metric 类证据 (现场实测数据权重最高), infra 次之, log/runbook 仅作辅助参考"
    ),
    # Judge 2: log/runbook-first bias
    _RCA_SYSTEM_PROMPT.replace(
        "优先看 metric 类证据 (现场实测), 次看 infra (运行环境/依赖), 再看 log/runbook",
        "**优先** log/runbook 类证据 (它们包含已知故障模式和历史经验), metric/infra 作交叉验证"
    ),
]

# ---- 文本相似度 ----
def _text_similarity(a: str, b: str) -> float:
    """计算两段文本的相似度 (0-1), 用于投票聚合."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower()[:200], b.lower()[:200]).ratio()


# ---- 单 Judge 调用 ----
async def _single_judge(
    judge_idx: int,
    candidates: list[dict[str, Any]],
    evidences: list[dict[str, Any]],
    temperature: float = 0.3,
) -> dict[str, Any] | None:
    """单个 Judge 的判定调用.

    Args:
        judge_idx: Judge 编号 (0-based), 用于选择 system prompt 变体
        candidates: EvidenceReducer 产出的候选列表
        evidences: 所有 Evidence
        temperature: LLM temperature (不同 Judge 可不同, 引入多样性)

    Returns:
        dict with root_cause, confidence, reasoning, etc. 失败返回 None.
    """
    try:
        from app.core.llm import get_chat_llm

        system_prompt = _JUDGE_VARIATIONS[
            min(judge_idx, len(_JUDGE_VARIATIONS) - 1)
        ]

        llm = get_chat_llm(model=settings.agent_report_model, temperature=temperature)

        # Build user prompt (same as original RCAJudge)
        lines: list[str] = ["候选根因 (确定性初排):"]
        for i, c in enumerate(candidates):
            lines.append(
                f"  C{i}: score={c.get('support_score', 0):.2f} type={c.get('type', '')} "
                f"agent={c.get('agent', '-')}\n"
                f"     candidate: {c.get('candidate', '')[:200]}\n"
                f"     evidence_ids: {c.get('evidence_ids', [])}"
            )
        lines.append("")
        lines.append("关键证据 summary (按 ev_i 引用):")
        for i, ev in enumerate(evidences):
            is_err = bool(
                (ev.get("metadata") or {}).get("error_type")
                or (ev.get("content") or {}).get("error")
            )
            marker = " [ERROR]" if is_err else ""
            lines.append(
                f"  ev_{i}{marker}: source={ev.get('source', '')} type={ev.get('type', '')}\n"
                f"     summary: {str(ev.get('summary') or '')[:200]}"
            )
        lines.append("")
        lines.append("请按系统约束输出 JSON。")

        resp = await llm.ainvoke([
            ("system", system_prompt),
            ("human", "\n".join(lines)),
        ])
        raw = getattr(resp, "content", "") or ""
        text = raw if isinstance(raw, str) else str(raw)

        # Parse JSON
        text = text.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text[:4].lower() == "json":
                text = text[4:]
        s, e = text.find("{"), text.rfind("}")
        if s == -1 or e <= s:
            raise ValueError(f"judge {judge_idx}: no JSON found in response")

        parsed = json.loads(text[s : e + 1])
        return {
            "root_cause": str(parsed.get("root_cause", ""))[:500],
            "ranked_candidates": list(parsed.get("ranked_candidates", [])),
            "supporting_evidence_ids": list(parsed.get("supporting_evidence_ids", [])),
            "reasoning": str(parsed.get("reasoning", ""))[:600],
            "confidence": float(parsed.get("confidence", 0.5)),
            "judge_idx": judge_idx,
        }
    except Exception as exc:
        logger.warning(f"[MultiJudge] Judge {judge_idx} failed: {type(exc).__name__}: {exc}")
        return None


# ---- 投票聚合 ----
def _aggregate_votes(
    judge_results: list[dict[str, Any]],
    fallback_candidate: dict[str, Any],
) -> dict[str, Any]:
    """对 K 个 Judge 的结果进行投票聚合.

    规则:
      1. 计算每个 Judge root_cause 之间的相似度 -> 相似度 > 0.5 视为"同意"同一根因
      2. 得票最多的 root_cause = 最终 root_cause
      3. confidence = mean(同意该根因的 judges' confidence) * 一致性因子
      4. 一致性因子 = 1.0 如果所有 judge 同意, 随分歧递减
    """
    if not judge_results:
        return _fallback_rca(fallback_candidate, "all judges failed")

    n = len(judge_results)
    if n == 1:
        r = judge_results[0]
        return {
            "root_cause": r["root_cause"],
            "ranked_candidates": r["ranked_candidates"],
            "supporting_evidence_ids": r["supporting_evidence_ids"],
            "reasoning": r["reasoning"],
            "confidence": r["confidence"],
            "via": "single_judge",
            "vote_detail": {"judges": 1, "agreement": 1.0, "judge_results": judge_results},
        }

    # 构建"同意组": 每对 judge 如果相似度 > 0.5, 视为同意
    # 使用 majority voting: root_cause 获得最多"支持票"的 judge 当选
    votes_for = [0] * n  # votes_for[i] = 有多少 judge 同意 judge i 的 root_cause
    for i in range(n):
        for j in range(n):
            if _text_similarity(
                judge_results[i]["root_cause"], judge_results[j]["root_cause"]
            ) > 0.5:
                votes_for[i] += 1

    # 得票最多的 judge (平票时取 confidence 更高的)
    best_idx = max(range(n), key=lambda i: (votes_for[i], judge_results[i]["confidence"]))
    best = judge_results[best_idx]

    # 一致性因子: 全部同意=1.0, 过半=0.85, 否则=0.7
    agreement_ratio = votes_for[best_idx] / n
    if agreement_ratio >= 1.0:
        consistency = 1.0
    elif agreement_ratio >= 0.67:
        consistency = 0.85
    elif agreement_ratio >= 0.5:
        consistency = 0.75
    else:
        consistency = 0.6

    # 同意该 root_cause 的 judges 的平均 confidence
    agreeing_judges = [
        j for j in judge_results
        if _text_similarity(j["root_cause"], best["root_cause"]) > 0.5
    ]
    avg_confidence = (
        sum(j["confidence"] for j in agreeing_judges) / len(agreeing_judges)
        if agreeing_judges else best["confidence"]
    )

    return {
        "root_cause": best["root_cause"],
        "ranked_candidates": best["ranked_candidates"],
        "supporting_evidence_ids": best["supporting_evidence_ids"],
        "reasoning": (
            f"[{n} Judge 投票, {votes_for[best_idx]}/{n} 同意] "
            + best["reasoning"][:500]
        ),
        "confidence": round(avg_confidence * consistency, 3),
        "via": "multi_judge",
        "vote_detail": {
            "judges": n,
            "votes_for_winner": votes_for[best_idx],
            "agreement_ratio": round(agreement_ratio, 3),
            "consistency_factor": consistency,
            "avg_confidence": round(avg_confidence, 3),
            "judge_results": [
                {
                    "idx": r["judge_idx"],
                    "root_cause": r["root_cause"][:120],
                    "confidence": r["confidence"],
                }
                for r in judge_results
            ],
        },
    }


def _fallback_rca(candidate: dict[str, Any], reason: str) -> dict[str, Any]:
    """确定性兜底: 取 Reducer 已排序的 candidates[0]."""
    top = candidate or {}
    return {
        "root_cause": str(top.get("candidate") or "(无候选)"),
        "ranked_candidates": [top.get("candidate", "")] if top else [],
        "supporting_evidence_ids": list(top.get("evidence_ids") or []),
        "reasoning": f"(确定性兜底: {reason})",
        "confidence": float(top.get("support_score") or 0.0),
        "via": "fallback",
    }


def _make_rca_evidence(rca: dict[str, Any]) -> dict[str, Any]:
    """把 RCA 判定结果包成 Evidence."""
    return {
        "source": str(EvidenceSource.RCA),
        "type": "rca",
        "summary": str(rca.get("root_cause") or "")[:500],
        "content": {"rca": rca},
        "metadata": {"agent": "rca_judge", "via": rca.get("via", ""), "multi_judge": True},
    }


# ---- 多Judge RCA 节点 (drop-in replacement for rca_judge_node) ----
async def multi_judge_rca_node(state: dict[str, Any]) -> dict[str, Any]:
    """Multi-Judge RCA 判定节点.

    与 rca_judge_node 接口完全兼容, 可直接在 deep graph 中替换使用。

    使用方式:
        # 在 deep_diagnosis_graph.py 中:
        from app.agents.multi_judge_rca import multi_judge_rca_node
        wf.add_node("rca_judge", multi_judge_rca_node)
    """
    num_judges = getattr(settings, "multi_judge_count", 3)
    candidates = state.get("candidates") or []
    evidences = state.get("evidences") or []

    if not candidates:
        logger.warning("[MultiJudge] 无候选, 跳过")
        rca = {
            "root_cause": "(无候选)", "ranked_candidates": [],
            "supporting_evidence_ids": [],
            "reasoning": "无候选可判定", "confidence": 0.0, "via": "empty",
        }
        return {
            "rca": rca,
            "evidences": [_make_rca_evidence(rca)],
            "transition_history": [
                make_transition("rca_judge", DEEP_RCA_JUDGED, "no candidates (multi_judge)")
            ],
        }

    # 并行调用 K 个 Judge
    t0 = time.perf_counter()
    tasks = []
    for k in range(num_judges):
        # 不同 Judge 使用略微不同的 temperature 引入多样性
        temp = 0.1 + (k * 0.15)  # 0.1, 0.25, 0.4
        tasks.append(_single_judge(k, candidates, evidences, temperature=min(temp, 0.5)))

    judge_results_raw = await asyncio.gather(*tasks, return_exceptions=True)
    judge_results = [
        r for r in judge_results_raw
        if isinstance(r, dict) and r is not None
    ]
    elapsed = time.perf_counter() - t0
    logger.info(
        f"[MultiJudge] {len(judge_results)}/{num_judges} judges succeeded "
        f"in {elapsed:.1f}s"
    )

    if not judge_results:
        # 全部失败 → 确定性回退
        logger.warning("[MultiJudge] All judges failed, using fallback")
        rca = _fallback_rca(candidates[0], "all judges failed")
    else:
        rca = _aggregate_votes(judge_results, candidates[0])

    logger.info(
        f"[MultiJudge] root_cause={rca['root_cause'][:60]!r} "
        f"conf={rca['confidence']:.3f} via={rca.get('via', 'unknown')}"
    )

    return {
        "rca": rca,
        "evidences": [_make_rca_evidence(rca)],
        "transition_history": [
            make_transition(
                "rca_judge", DEEP_RCA_JUDGED,
                f"via={rca.get('via', '')} conf={rca['confidence']:.2f} "
                f"judges={len(judge_results)}/{num_judges}"
            ),
        ],
    }


# ---- 对比测试: 同时运行单 Judge 和多 Judge, 输出对比报告 ----
async def compare_single_vs_multi(
    state: dict[str, Any],
    num_judges: int = 3,
) -> dict[str, Any]:
    """运行单 Judge 和多 Judge 两种模式, 输出对比报告.

    用于验证 Multi-Judge 投票的实际效果:
      - 单 Judge confidence vs 多 Judge 加权 confidence
      - 一致性因子
      - 各 Judge 的 root_cause 差异

    Returns:
        {
            "single": {...},   # 单 Judge 结果
            "multi": {...},    # 多 Judge 结果
            "comparison": {
                "confidence_delta": ...,  # 置信度变化
                "root_cause_changed": ..., # 根因是否变化
                "judges_agreement": ...,  # Judge 间一致性
            }
        }
    """
    from app.diagnosis_graphs.deep_diagnosis_graph import rca_judge_node

    # 单 Judge
    single_result = await rca_judge_node(state)

    # 多 Judge
    multi_result = await multi_judge_rca_node(state)

    single_rca = single_result.get("rca", {})
    multi_rca = multi_result.get("rca", {})
    vote_detail = multi_rca.get("vote_detail", {})

    comparison = {
        "single_confidence": single_rca.get("confidence", 0),
        "multi_confidence": multi_rca.get("confidence", 0),
        "confidence_delta": round(
            multi_rca.get("confidence", 0) - single_rca.get("confidence", 0), 3
        ),
        "root_cause_changed": _text_similarity(
            single_rca.get("root_cause", ""), multi_rca.get("root_cause", "")
        ) < 0.8,
        "root_cause_similarity": round(
            _text_similarity(
                single_rca.get("root_cause", ""), multi_rca.get("root_cause", "")
            ), 3
        ),
        "judges_agreement": vote_detail.get("agreement_ratio", 0),
        "single_via": single_rca.get("via", ""),
        "multi_via": multi_rca.get("via", ""),
    }

    logger.info(
        f"[MultiJudge Compare] "
        f"single_conf={comparison['single_confidence']:.3f} "
        f"multi_conf={comparison['multi_confidence']:.3f} "
        f"delta={comparison['confidence_delta']:+.3f} "
        f"agreement={comparison['judges_agreement']:.0%} "
        f"changed={comparison['root_cause_changed']}"
    )

    return {
        **multi_result,
        "single_judge_result": single_result,
        "comparison": comparison,
    }
