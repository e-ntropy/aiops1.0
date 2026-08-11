"""确定性生成扩展后的离线 Benchmark 数据。"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "benchmark"
NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)
LIFECYCLE_SEED_IDS = (
    "lc-recovered-close",
    "lc-corrected-root-close",
    "lc-critical-remains",
    "lc-missing-baseline",
    "lc-wrong-scope-baseline",
    "lc-collector-failure",
    "lc-diagnosis-rejected",
    "lc-plan-rejected",
    "lc-redaction-denied",
)
DIAGNOSIS_SEED_IDS = (
    "fx-host-disk-simple",
    "fx-host-memory-simple",
    "fx-network-port-simple",
    "fx-healthy-negative",
    "fx-transient-recovered-negative",
    "fx-mysql-lock-complex",
    "fx-redis-pool-complex",
    "fx-k8s-oom-complex",
    "fx-kafka-lag-complex",
    "fx-jvm-fullgc-complex",
    "fx-nginx-upstream-complex",
    "fx-partial-source-boundary",
    "fx-all-sources-down-boundary",
    "fx-conflicting-evidence-boundary",
    "fx-noisy-log-negative",
    "fx-deep-report-missing-boundary",
)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(
            "".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                for row in rows
            )
        )


GOALS = {
    "knowledge_qa": [["知识", "来源"], ["回答"]],
    "status_query": [["当前状态"], ["指标"]],
    "system_inspection": [["健康快照"], ["异常"], ["只读建议"]],
    "fault_diagnosis": [["故障作用域"], ["最小证据"], ["根因"]],
    "optimization": [["资源基线"], ["优化机会"], ["优化计划"]],
    "capacity_performance": [["资源基线"], ["容量余量"], ["数据"]],
    "incident_review": [["时间线"], ["根因", "行动项"]],
    "evaluation": [["评测范围"], ["评估"]],
    "out_of_scope": [["AIOps", "范围"]],
}


def query_rows() -> list[dict[str, Any]]:
    families = [
        ("knowledge", "请说明 {topic}是什么以及基本原理", "knowledge_qa", "knowledge_qa", "ready", "none", False, "read_only"),
        ("status_local", "查看本机{topic}当前状态和占用", "status_query", "system_status", "ready", "local_host", False, "read_only"),
        ("status_missing", "查看{topic}当前状态", "status_query", "system_status", "clarifying", "none", True, "read_only"),
        ("status_remote", "查看 node-{n}.example.com 的 {topic} status", "status_query", "system_status", "ready", "remote_host", False, "read_only"),
        ("inspection", "对本机进行{topic}巡检，禁止执行变更", "system_inspection", "system_inspection", "ready", "local_host", False, "read_only"),
        ("diagnosis", "本机{topic}异常，请排查根因且不要重启", "fault_diagnosis", "adaptive_diagnosis", "ready", "local_host", False, "read_only"),
        ("optimization", "分析本机{topic}并给出优化建议，不要修改配置", "optimization", "readonly_optimization", "ready", "local_host", False, "read_only"),
        ("capacity", "分析本机{topic}的容量规划、资源余量和性能瓶颈", "capacity_performance", "capacity_performance", "ready", "local_host", False, "read_only"),
        ("review", "复盘上次{topic}事故并整理时间线", "incident_review", "incident_review", "ready", "none", False, "read_only"),
        ("evaluation", "运行 benchmark 评测{topic}诊断流程", "evaluation", "evaluation", "ready", "none", False, "read_only"),
        ("out_of_scope", "帮我{topic}", "out_of_scope", "", "failed", "none", False, "read_only"),
        ("high_risk", "重启本机{topic}并排查异常", "fault_diagnosis", "adaptive_diagnosis", "clarifying", "local_host", True, "high"),
    ]
    topics = [
        "CPU", "内存", "磁盘", "进程", "Redis", "MySQL", "Nginx", "Kafka", "容器", "网络",
        "错误预算", "SLO", "告警", "端口", "JVM", "缓存", "连接池", "日志", "延迟", "可用性",
    ]
    out_topics = [
        "写一首诗", "制定旅游计划", "解释量子力学", "推荐电影", "翻译文章", "设计海报", "计算菜谱热量", "写小说",
        "分析股票", "准备英语考试", "总结历史", "规划健身", "推荐音乐", "解决数学题", "润色邮件", "介绍摄影",
        "安排课程", "设计游戏角色", "写广告文案", "推荐餐厅",
    ]
    high_risk_queries = [
        "停止本机高 CPU 进程并排查卡顿",
        "结束本机占用内存最高的进程并排查 OOM",
        "删除本机旧日志并排查磁盘空间不足",
        "停止本机异常 worker 进程并排查请求失败",
        "重启本机 Redis 服务并排查连接异常",
        "重启本机 MySQL 服务并排查连接超时",
        "修改本机 Nginx 配置并排查 502",
        "重启本机 Kafka 消费者并排查消息积压",
        "删除本机异常容器并排查服务失败",
        "修改本机网络配置并排查连接超时",
        "修改本机告警阈值并排查错误预算异常",
        "调整本机服务限流配置并排查 SLO 下降",
        "删除本机重复告警规则并排查告警风暴",
        "停止本机占用端口的进程并排查启动失败",
        "修改本机 JVM 堆配置并排查 Full GC",
        "清理本机缓存并排查命中率异常",
        "修改本机连接池配置并排查连接耗尽",
        "删除本机应用日志并排查磁盘告警",
        "重启本机网关并排查延迟异常",
        "回滚本机服务版本并排查可用性下降",
    ]
    rows: list[dict[str, Any]] = []
    for family_index, family in enumerate(families):
        name, template, intent, capability, phase, scope, confirmation, risk = family
        for index in range(20):
            topic = out_topics[index] if name == "out_of_scope" else topics[index]
            query = (
                high_risk_queries[index]
                if name == "high_risk"
                else template.format(topic=topic, n=index + 1)
            )
            rows.append(
                {
                    "id": f"wf2-{name}-{index + 1:02d}",
                    "family_id": f"query-{name}",
                    "scenario": name,
                    "task_class": "boundary" if name in {"status_missing", "high_risk", "out_of_scope"} or index % 4 == 0 else "normal",
                    "difficulty": "complex" if index % 3 == 0 or name in {"high_risk", "status_missing"} else "simple",
                    "query": query,
                    "expected_intent": intent,
                    "expected_capability": capability,
                    "expected_phase": phase,
                    "expected_scope_kind": scope,
                    "expected_confirmation": confirmation,
                    "expected_risk": risk,
                    "subtask_term_groups": GOALS[intent],
                    "variant_index": family_index * 20 + index,
                }
            )
    return rows


def lifecycle_rows() -> list[dict[str, Any]]:
    seeds = _read_jsonl(BENCH / "lifecycle_contract_eval.jsonl")
    seeds = seeds[:9]
    for seed_index, seed in enumerate(seeds):
        seed["seed_id"] = LIFECYCLE_SEED_IDS[seed_index]
        for key in ("family_id", "variant_index", "task_class", "difficulty"):
            seed.pop(key, None)
    rows: list[dict[str, Any]] = []
    for index in range(120):
        seed = copy.deepcopy(seeds[index % len(seeds)])
        variant = index // len(seeds)
        original_id = str(seed["seed_id"])
        seed["id"] = f"lc2-{original_id.removeprefix('lc-')}-{index + 1:03d}"
        seed["family_id"] = f"lifecycle-{original_id}"
        seed["variant_index"] = variant
        seed["task_class"] = "boundary" if seed["scenario"] != "happy_path" else "normal"
        seed["difficulty"] = "complex" if index % 3 else "simple"
        for field in ("baseline", "recovery"):
            if isinstance(seed.get(field), dict):
                seed[field]["cpu"] = 20 + (index % 17)
                seed[field]["disk"] = 35 + (index % 19)
        rows.append(seed)
    return rows


def diagnosis_rows() -> list[dict[str, Any]]:
    seeds = _read_jsonl(BENCH / "diagnosis_fixture_eval.jsonl")
    seeds = seeds[:16]
    for seed_index, seed in enumerate(seeds):
        seed["query"] = str(seed.get("base_query") or seed["query"])
        seed["seed_id"] = DIAGNOSIS_SEED_IDS[seed_index]
        for key in ("family_id", "variant_index", "base_query"):
            seed.pop(key, None)
    rows: list[dict[str, Any]] = []
    for index in range(120):
        seed = copy.deepcopy(seeds[index % len(seeds)])
        variant = index // len(seeds)
        original_id = str(seed.get("seed_id") or seed["id"])
        seed["id"] = f"fx2-{original_id.removeprefix('fx-')}-{index + 1:03d}"
        seed["seed_id"] = original_id
        seed["family_id"] = f"diagnosis-{original_id}"
        seed["variant_index"] = variant
        seed["base_query"] = seed["query"]
        seed["query"] = f"{seed['query']}（脱敏场景 {variant + 1}）"
        for stage in ("fast", "deep"):
            for evidence in (seed.get(stage) or {}).get("evidence") or []:
                evidence.setdefault("content", {})["variant"] = variant
        rows.append(seed)
    return rows


def _record(
    identifier: str,
    *,
    tier: str,
    status: str,
    confidence: float,
    session_id: str = "",
    incident_id: str = "",
    service: str = "",
    scope_key: str = "",
    redaction_passed: bool = False,
    expires_at: datetime | None = None,
    superseded_by: str = "",
) -> dict[str, Any]:
    return {
        "id": identifier,
        "tier": tier,
        "status": status,
        "confidence": confidence,
        "session_id": session_id,
        "incident_id": incident_id,
        "service": service,
        "scope_key": scope_key,
        "redaction_passed": redaction_passed,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "superseded_by": superseded_by,
    }


def memory_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    scenarios = (
        "session_isolation", "incident_isolation", "candidate_rejection", "scope_isolation",
        "expired_rejection", "superseded_rejection", "redaction_gate", "generic_verified",
        "service_isolation", "empty_scope_isolation", "mixed_distractors", "service_alias_boundary",
    )
    for index in range(240):
        scenario = scenarios[index % len(scenarios)]
        suffix = f"{index + 1:03d}"
        session = f"session-{index % 17}"
        incident = f"incident-{index % 13}"
        service = f"service-{index % 11}"
        scope = f"service|prod|{service}|node-{index % 7}"
        good = f"good-{suffix}"
        candidate_confidence = (0.99, 0.92, 0.61, 0.74, 0.985)[index % 5]
        session_confidence = (0.98, 0.89, 0.58, 0.995, 0.72, 0.81, 0.94)[index % 7]
        scope_confidence = (0.97, 0.91, 0.64, 0.77, 0.96, 0.69)[index % 6]
        expired_confidence = (0.96, 0.87, 0.60, 0.93, 0.75)[index % 5]
        superseded_confidence = (0.95, 0.85, 0.57, 0.90, 0.68)[index % 5]
        records = [
            _record(good, tier="verified_knowledge", status="verified", confidence=0.975, service=service, scope_key=scope, redaction_passed=True),
            _record(f"candidate-{suffix}", tier="candidate", status="candidate", confidence=candidate_confidence, service=service, scope_key=scope),
            _record(f"foreign-session-{suffix}", tier="session", status="active", confidence=session_confidence, session_id=f"other-{session}"),
            _record(f"foreign-scope-{suffix}", tier="verified_knowledge", status="verified", confidence=scope_confidence, service=service, scope_key=f"{scope}-other", redaction_passed=True),
            _record(f"expired-{suffix}", tier="verified_knowledge", status="verified", confidence=expired_confidence, service=service, scope_key=scope, redaction_passed=True, expires_at=NOW - timedelta(days=1)),
            _record(f"superseded-{suffix}", tier="verified_knowledge", status="verified", confidence=superseded_confidence, service=service, scope_key=scope, redaction_passed=True, superseded_by=good),
        ]
        expected = [good]
        if scenario == "session_isolation":
            own = f"own-session-{suffix}"
            records.append(_record(own, tier="session", status="active", confidence=0.83, session_id=session))
            expected.append(own)
        elif scenario == "incident_isolation":
            own = f"own-incident-{suffix}"
            records.append(_record(own, tier="incident", status="active", confidence=0.84, incident_id=incident))
            expected.insert(0, own)
        elif scenario == "redaction_gate":
            records.append(_record(f"unredacted-{suffix}", tier="verified_knowledge", status="verified", confidence=1.0, service=service, scope_key=scope, redaction_passed=False))
        elif scenario == "generic_verified":
            generic = f"generic-{suffix}"
            records.append(_record(generic, tier="verified_knowledge", status="verified", confidence=0.79, redaction_passed=True))
            expected.append(generic)
        elif scenario == "service_isolation":
            records.append(_record(f"foreign-service-{suffix}", tier="verified_knowledge", status="verified", confidence=1.0, service="other-service", scope_key=scope, redaction_passed=True))
        elif scenario == "empty_scope_isolation":
            service = ""
            scope = ""
            records[0] = _record(good, tier="verified_knowledge", status="verified", confidence=0.975, redaction_passed=True)
        elif scenario == "mixed_distractors":
            own = f"own-session-{suffix}"
            records.append(_record(own, tier="session", status="active", confidence=0.80, session_id=session))
            expected.append(own)
        elif scenario == "service_alias_boundary":
            # 当前策略坚持精确身份，不自行把别名映射为同一服务；Gold 记录该能力缺口。
            alias = f"alias-{suffix}"
            records.append(_record(alias, tier="verified_knowledge", status="verified", confidence=0.88, service=f"{service}-api", scope_key=scope, redaction_passed=True))
            expected.append(alias)
        forbidden = [item["id"] for item in records if item["id"] not in expected]
        single_expected = scenario in {
            "candidate_rejection",
            "scope_isolation",
            "expired_rejection",
            "superseded_rejection",
            "redaction_gate",
            "service_isolation",
            "empty_scope_isolation",
        }
        recall_limit = 1 if single_expected and (index // len(scenarios)) % 2 == 0 else 3
        rows.append(
            {
                "id": f"mem-{suffix}",
                "family_id": f"memory-{scenario}",
                "scenario": scenario,
                "task_class": "boundary" if scenario != "session_isolation" else "normal",
                "difficulty": "complex" if index % 3 else "simple",
                "context": {"session_id": session, "incident_id": incident, "service": service, "scope_key": scope, "limit": recall_limit, "now": NOW.isoformat()},
                "records": records,
                "expected_ids": expected,
                "forbidden_ids": forbidden,
                "promotion_context": {
                    "intent": "fault_diagnosis",
                    "incident_id": incident,
                    "workflow_completed": True,
                    "human_root_cause_confirmed": index % 5 != 0,
                    "remediation_verified": index % 7 != 0,
                    "incident_closed": index % 11 != 0,
                    "redaction_passed": index % 13 != 0,
                },
            }
        )
    for row in rows:
        ctx = row["promotion_context"]
        row["expected_promote"] = all(
            ctx[key]
            for key in (
                "workflow_completed", "human_root_cause_confirmed", "remediation_verified",
                "incident_closed", "redaction_passed",
            )
        )
    return rows


def tool_rows() -> list[dict[str, Any]]:
    fallback = [
        ("timeout_retry", "TimeoutError", 1, 2, False, False, "retry", 500),
        ("connection_retry", "ConnectionError", 2, 2, False, False, "retry", 1000),
        ("rate_limit_alternative", "RateLimitError", 3, 2, True, False, "use_alternative", 0),
        ("required_source_failure", "ServiceUnavailable", 3, 2, False, True, "fail_workflow", 0),
        ("permission_denied", "PermissionError", 1, 3, True, False, "deny", 0),
        ("validation_confirm", "ValidationError", 1, 3, False, False, "request_confirmation", 0),
    ]
    envelopes = [
        ("readonly_allowed", True, "local_host", "read_only", "allow", "", 15, True),
        ("scope_missing", False, "none", "read_only", "allow", "", 15, False),
        ("permission_deny", True, "local_host", "read_only", "deny", "", 15, False),
        ("write_no_idempotency", True, "local_host", "high", "ask", "", 15, False),
        ("write_idempotent", True, "local_host", "high", "ask", "idem-fixed", 15, True),
        ("timeout_invalid", True, "local_host", "read_only", "allow", "", 301, False),
    ]
    rows: list[dict[str, Any]] = []
    for repeat in range(10):
        for name, error, attempt, maximum, alternative, required, action, delay in fallback:
            rows.append({"id": f"tool-fallback-{name}-{repeat:02d}", "family_id": f"tool-{name}", "kind": "fallback", "task_class": "boundary" if action != "retry" else "normal", "difficulty": "complex" if repeat % 3 else "simple", "error_type": error, "attempt": attempt, "max_retries": maximum, "has_alternative": alternative, "required_source": required, "expected_action": action, "expected_retry_after_ms": delay})
        for name, validated, scope_kind, risk, permission, idem, timeout, valid in envelopes:
            rows.append({"id": f"tool-envelope-{name}-{repeat:02d}", "family_id": f"tool-{name}", "kind": "envelope", "task_class": "boundary" if not valid else "normal", "difficulty": "complex" if repeat % 2 else "simple", "scope_validated": validated, "scope_kind": scope_kind, "risk_level": risk, "permission_decision": permission, "idempotency_key": f"{idem}-{repeat}" if idem else "", "timeout_sec": timeout, "expected_valid": valid})
    return rows


def main() -> None:
    datasets = {
        "workflow_contract_eval.jsonl": query_rows(),
        "lifecycle_contract_eval.jsonl": lifecycle_rows(),
        "diagnosis_fixture_eval.jsonl": diagnosis_rows(),
        "memory_governance_eval.jsonl": memory_rows(),
        "tool_safety_eval.jsonl": tool_rows(),
    }
    for name, rows in datasets.items():
        _write_jsonl(BENCH / name, rows)
        print(f"{name}: {len(rows)}")


if __name__ == "__main__":
    main()
