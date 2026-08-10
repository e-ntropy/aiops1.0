# 第一条简历：双模式 Agent 编排——代码映射

## 包装后的简历原句

> 面向常规问答与复杂故障诊断，基于 LangGraph 构建 Fast/Deep 双状态图，分别执行路由、规划、动态重规划及日志、指标、基础设施、Runbook Agent 并行分析；通过步数预算、重复检测和异常降级保证任务有界执行。

## 面试主叙事

这条可以作为项目的架构主线来讲：系统不是用一条万能 Agent 链处理所有问题，而是把低成本、可迭代的 Fast Plan-Execute-Replan 与高覆盖、多证据的 Deep Specialist fan-out/fan-in 分开。包装允许把二者统称为“双模式 Agent 编排”，但源码层要准确说明：动态 Replan、Skill reroute、步数和重复检测集中在 Fast；四类专家并行、Evidence 汇聚和 RCA 集中在 Deep。

## 原子能力映射

| 原子能力 | 文件与符号 | 上游→下游 | State/配置 | 主链状态 |
| --- | --- | --- | --- | --- |
| 双独立 StateGraph | `app/agents/graph.py::build_aiops_graph()` L95；`app/diagnosis_graphs/deep_diagnosis_graph.py::build_deep_graph()` L894 | runner 选择→各自 `astream()` | 两套 TypedDict | 已进入主链 |
| Fast/Deep 模式选择 | `app/orchestration/diagnosis_runner.py::resolve_effective_mode()` L80、`run_diagnosis_graph()` L100 | API/Worker mode→graph | `deep_diagnosis_enabled` | 已进入主链 |
| Alert 自动模式规则 | `app/api/v1/webhook.py::_diagnosis_mode_for()` L81 | severity/告警数→mode | critical/page/p0/p1 或 ≥10 | 已进入 Webhook 主链 |
| Skill Router | `app/agents/skill_router.py::skill_router_node()` L93 | input+metadata menu→selected_skill/OOS | `selected_skill`,`response` | Fast 主链 |
| Planner | `app/agents/planner.py::plan_node()` L27 | selected Skill Playbook→steps | `plan`,`iteration` | Fast 主链 |
| Executor | `app/agents/executor.py::execute_node()` L90 | plan[0]→Tool Agent→result | `past_steps` add, `iteration` | Fast 主链 |
| Dynamic Replan | `app/agents/replanner.py::replan_node()` L207 | plan+past_steps→response/new plan | `plan`,`response` | Fast 主链 |
| Skill reroute | `replanner.py::_validate_reroute()` L160、reroute 分支 L301 | evidence history→new Skill→Planner | `reroute_count`,`tried_skills`,`pending_reroute` | Fast 主链 |
| 条件边 | `app/agents/graph.py::route_after_skill()` L81、`should_end()` L56 | Router/Replanner→END/Planner/Executor | response/plan/reroute flag | Fast 主链 |
| Deep EvidencePlan | `deep_diagnosis_graph.py::evidence_plan_node()` L317 | input keyword→agent set | `evidence_plan` | Deep 主链 |
| Log Agent | `app/agents/log_agent.py::run_log_agent()` L96 | input→KB log pattern→Evidence | `evidences` add | Deep 主链 |
| Metric Agent | `app/agents/metric_agent.py::run_metric_agent()` L109 | input→Prom/local metrics→Evidence | `evidences` add | Deep 主链 |
| Infra Agent | `app/agents/infra_agent.py::run_infra_agent()` L108 | input→system/MCP infra→Evidence | `evidences` add | Deep 主链 |
| Runbook Agent | `app/agents/runbook_agent.py::run_runbook_agent()` L81 | input→SOP KB→Evidence | `evidences` add | Deep 主链 |
| fan-out/fan-in | Deep 构图 L917-L920 | EvidencePlan→4 nodes→Reducer | `Annotated[List,operator.add]` | 已实现；未派遣节点 skip |
| Evidence 冲突归并 | `evidence_reducer_node()` L432；`rca_judge_node()` L589 | Evidence→candidate→RCA | `candidates`,`rca` | Deep 主链 |
| 步数预算 | `app/config.py` L369；`agent_harness.py::evaluate_replanner_pre_llm()` L708 | iteration≥max→force report | 默认 5 | Fast 主链 |
| 重复检测 | `agent_harness.py::_has_repeated_steps()` L803 | 最近 3 个 step fingerprint→force report | repeat window=3 | Fast 主链 |
| Graph recursion | `graph_recursion_limit()` L430；runner L171 | max_steps×3+5 | LangGraph config | Fast/Deep 共用 runner config |
| Tool 内并行 | `app/runtime/tool_runner.py::partition_tool_calls()` L76、`run_parallel_agent()` L203 | safe Tool batch→`asyncio.gather` | max_parallel | Fast 与各 Specialist 内部 |
| 节点降级 | Router/Planner/Replanner except；四 Specialist except；RCA fallback | error→规则/模板/error Evidence | transitions | 已实现 |
| Graph checkpoint | 两图 `compile()` 无参数 | — | 无 checkpointer/thread_id | 主链未实现 |
| Deep 整体回落 Fast | runner 仅在 Deep 开关关闭前置回落 | graph error→error event | — | 运行中自动回落未实现 |

## 关键调用链

```text
POST /api/v1/aiops/diagnose
-> aiops.py::aiops_diagnose
-> aiops_service.py::stream_diagnose
-> diagnosis_runner.py::run_diagnosis_graph
-> resolve_effective_mode
-> graph.astream
-> _convert_node_event
-> SSE
```

```text
Alertmanager/submit
-> IncidentRepository 创建任务
-> RedisIncidentQueue.enqueue_task
-> DiagnosisWorker.handle_message
-> run_legacy_langgraph_with_audit
-> run_diagnosis_graph(Fast/Deep)
-> Postgres 审计/任务完成
-> XACK
```

## 面试时的边界句

- “双模式”是真实的两个独立图，不是一个 if/else 包装的同一条链。
- “动态重规划”特指 Fast；Deep 是固定 DAG 中的动态派遣，不是 Replan 循环。
- “并行分析”是真实的图级 fan-out/fan-in，但 EvidencePlan 可以让某些节点快速跳过。
- “异常降级”主要是节点级 graceful degradation；图级失败由 SSE error 或 Worker retry/DLQ 接管。
- “有界”由 Fast max step/重复/recursion 与 Deep 固定 DAG/max_iters/Worker timeout共同构成，不等于所有入口都有同一种硬超时。

