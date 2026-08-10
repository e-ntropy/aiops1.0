# LangGraph 与端到端执行链

## 1. Fast State 与写入者

State：`app/agents/state.py::PlanExecuteState` L34-L72。

| 字段 | Reducer/语义 | 主要写入者 | 主要读取者 |
| --- | --- | --- | --- |
| `input` | 覆盖；原始现象 | runner 初始输入 | Router/Planner/Deep specialists |
| `diagnosis_mode`, `requested_diagnosis_mode` | 覆盖 | runner | 观测/报告 |
| `alert_signature` | 覆盖 | Worker audit 计算后传 runner | Router Wiki recall |
| `selected_skill`, `skill_reason` | 覆盖 | SkillRouter；合法 reroute 时 Replanner | Planner、Executor tool filter |
| `plan` | 覆盖 | Planner；Replanner | Executor、条件边 |
| `past_steps` | `operator.add` | Executor 每次追加 `(step,result)` | Replanner/报告 |
| `response` | 覆盖；非空终止 | Router(OOS)、Replanner | 条件边/事件转换 |
| `iteration` | 覆盖 | Planner 重置 0；Executor +1 | Harness/步数保护 |
| `permission_mode` | 覆盖 | 可由调用态提供，否则配置默认 | Executor |
| `transition_history` | `operator.add` | 各节点 | runner 转 SSE transition |
| `reroute_count` | 覆盖 | Replanner | reroute 上限校验 |
| `tried_skills` | `operator.add` | Replanner | 候选过滤/防回环 |
| `pending_reroute` | 覆盖 | Replanner=True；Planner=False | 条件边 |

### Fast 节点逐步执行

1. `skill_router_node()`（`skill_router.py` L93-L199）只把 `registry.to_router_menu()` 的元数据卡片给 LLM；成功写 `selected_skill`，OOS 则直接写 `response`。LLM 失败时 `_build_router_fallback_result()` 用关键词规则决定 OOS 或 `generic_oncall`。
2. `plan_node()`（`planner.py` L27-L100）这时才从 Registry 取完整 `skill.playbook` 注入 Planner，结构化输出 `Plan.steps`。这是“元数据先路由、Playbook 后加载”的真实渐进披露点。
3. `execute_node()`（`executor.py` L90-L177）取 `plan[0]`，以 Skill/PermissionMode 过滤工具，运行一个工具型 Agent，把结果追加到 `past_steps`。注意它不直接从 plan 删除第一步；删除/替换发生在 Replanner。
4. `replan_node()`（`replanner.py` L207-L405）先走 Harness 的确定性预判，再决定调用 Replanner LLM。返回 `new_plan` 时是**覆盖旧 plan**，不是 merge；`past_steps` 依靠 reducer 保留。
5. `should_end()`（`graph.py` L56-L78）按 `response`、`pending_reroute`、空 plan、默认继续四级顺序路由。

## 2. Fast 有界执行

### 步数预算

- 配置：`app/config.py::agent_max_steps` L369，默认 5。
- Executor 前置保护：`executor.py` L112-L125；只有 `iteration > max` 才返回中止结果。
- Replanner 前置保护：`agent_harness.py::evaluate_replanner_pre_llm()` L708-L741；`iteration >= max` 立即 `force_report`。
- LangGraph 递归限制：`agent_harness.py::graph_recursion_limit()` L430-L431，公式 `max_steps * 3 + 5`；runner 通过 config 传入（`diagnosis_runner.py` L171-L182）。

因此真正的主保护是 Replanner 的 `>=`，Executor 的 `>` 是第二道保险。Token/时间预算与步数预算不同：Harness 在执行完成后汇总 usage 并发告警事件（runner L243-L269），它不是中途强制停止 Token 的硬配额。

### 重复检测

`AgentHarness._has_repeated_steps()`（L803-L819）只检查最近 3 个 step：保留字母数字/中文、转小写、截前 100 字，三个 fingerprint 完全相同才强制报告。它能挡住完全重复循环，但不能识别语义改写后的重复步骤。Replanner 对新 plan 仅过滤空字符串（`replanner.py` L382-L404），没有与 `past_steps` 做代码级语义去重；“已完成步骤不要重复”主要依赖 Prompt。

### 动态 Replan 与 Skill reroute

- 一般 Replan：Replanner 的 `Act(is_finished=False, plan=[...])` 直接覆盖 `state.plan`。
- Skill reroute：LLM 提议 `should_reroute/new_skill/reason`，代码 `_validate_reroute()` L160-L204 再验证：至少已有 2 步证据（默认）、不超过 1 次（默认）、不选当前 Skill、不选 tried skill、目标必须存在。
- 合法后：旧 plan 清空、`selected_skill` 覆盖、旧 Skill 追加到 `tried_skills`、`pending_reroute=True`；条件边回 Planner，Planner 重新加载新 Playbook 并清标志。
- Replan LLM 失败、空 plan、达到步数、重复步骤都用 `_force_summary()` 生成保守报告。

## 3. Deep State 与写入者

State：`app/agents/state_deep.py::DeepDiagnosisState` L25-L57。

| 字段 | Reducer/语义 | 写入节点 |
| --- | --- | --- |
| `input/modes/signature` | 覆盖 | runner |
| `transition_history` | `operator.add` | 所有节点 |
| `task_id/incident_group_id/incident_id` | 覆盖 | 理论上调用者/IncidentManager 补齐；当前 runner 未传 |
| `evidence_plan` | 覆盖 | EvidencePlan |
| `evidences` | `operator.add`，并行合并核心 | CorrelationContext、四 Specialist、RCAJudge |
| `candidates` | 覆盖 | EvidenceReducer |
| `rca` | 覆盖 | RCAJudge |
| `remediation` | 覆盖 | RemediationPlanner |
| `response` | 覆盖 | ReportAgent |

### Deep 节点与边

| 节点 | 实现 | 输入→输出 | 降级 |
| --- | --- | --- | --- |
| IncidentManager | `incident_manager_node()` L106 | task/group→上下文字段/transition | 无 task 或 DB 错误时继续 |
| CorrelationContext | L166 | group/Wiki→incident history Evidence | DB/Wiki best-effort |
| EvidencePlan | L317 | input→`{agents,strategy}` | 无匹配默认 Metric+Log |
| 四 Specialist | `app/agents/{log,metric,infra,runbook}_agent.py::run_*` | 隔离输入→各 1 条 Evidence | 捕获异常并返回 error Evidence |
| EvidenceReducer | L432 | evidences→排序 candidates | 无 Evidence 返回空候选 |
| RCAJudge | L589 | candidate+summary→RCA + RCA Evidence | LLM/JSON 失败取 top candidate |
| RemediationPlanner | L716 | RCA→只读/写操作建议 | 确定性模板/default |
| ReportAgent | L796 | 全部结构化结果→Markdown response | 无额外 LLM，不抛写操作 |

### 并行与冲突

四个节点由同一上游静态扇出，并都指向 Reducer（构图 L917-L920）；`evidences` 的 `operator.add` 允许并发 patch 合并。各 Agent 互不读取对方私有消息，只返回摘要 Evidence。冲突不在 Specialist 间协商：Reducer 以 source 权重/错误标识做确定性排序，RCAJudge 再基于 summary 重排。当前没有“多数投票”；`multi_judge_rca.py` 存在但未在 `build_deep_graph()` 接线。

### Deep 的有界性

Deep 没有 Fast 的 plan/replan/iteration；有界性来自固定 DAG 和每个 Specialist 的 `run_parallel_agent(max_iters=3/4)`。单个 Specialist 没有外层 `asyncio.wait_for`，但 Worker 对整次诊断有 `diagnosis_task_timeout_sec`（`diagnosis_worker.py` L140-L143）；同步 SSE 路径没有同等整图 timeout。工具/LLM 自身还可能有各自超时。

## 4. Fast/Deep 选择

1. `normalize_diagnosis_mode()`（runner L60-L77）归一化别名，未知值默认 Fast。
2. `resolve_effective_mode()`（L80-L97）仅在请求 Deep 且 `deep_diagnosis_enabled=True` 时选 Deep，否则 Fast。
3. 人工诊断模式由请求体给出。
4. Alertmanager 规则：severity 为 critical/page/p0/p1，或同 payload 告警数 ≥10 时 Deep；否则 Fast（`webhook.py` L81-L88）。

不存在基于问题复杂度的 LLM 自动模式分类器。简历中“面向常规问答与复杂故障诊断”是产品定位，代码里的实际选择是显式参数/告警规则/功能开关。

## 5. 工具、Skill 与权限链

```text
SKILL.md frontmatter/body
  -> loader: Skill(metadata + playbook + allowed_tools)
  -> Router 只见 to_router_card(metadata)
  -> Planner 读取完整 playbook
  -> Executor get_all_tools()
  -> filter_tools_for_skill(skill, mode)
  -> evaluate_permission(): allow / ask / deny
  -> LLM 只能 bind visible tools
  -> tool_runner 再检查工具名是否在 tools_by_name
  -> ask: PostgreSQL approval_requests 等人工决定
  -> 按 concurrency_safe 分批，并行只读/安全 Tool，写/不安全 Tool 串行
```

执行前二次防线证据：`tool_runner.py` L335-L418。模型伪造未绑定 Tool 名会收到拒绝 ToolMessage，不会执行。

## 6. 后台可靠性时序

```text
API: DB insert/upsert task -> XADD -> save queue_message_id
Worker: XAUTOCLAIM stale OR XREADGROUP new
      -> DB status=running, attempts+1
      -> global slot + wait_for(graph)
      -> success: DB succeeded -> XACK
      -> retryable/current implementation treats any caught Exception similarly:
           DB pending -> XADD new -> save new id -> XACK old
      -> attempts exhausted/bad message:
           DB failed when possible -> XADD DLQ -> XACK old
```

语义是 at-least-once，不是 exactly-once。已成功任务的重复消息会查 PostgreSQL 后直接 ACK（worker L101-L106）。但崩溃窗口仍存在：例如 DB 已成功、ACK 前崩溃会重投，幂等查询可挡重复执行；而 retry 的 DB→XADD→ACK 跨两个系统无事务，任一步失败都可能留下状态/消息偏差，需要运维对账。

当前没有指数退避或错误分类接入 Worker 重试；所有普通 Exception 按 attempts 立即重新 XADD。Harness 有 `classify_error()`，但 Worker 未调用它。

## 7. Checkpoint、暂停与恢复结论

- Fast `workflow.compile()`：`app/agents/graph.py` L133，无 checkpointer。
- Deep `wf.compile()`：`deep_diagnosis_graph.py` L927，无 checkpointer。
- runner config 只有 `recursion_limit`，没有 `configurable.thread_id`。
- `app/agents/hitl.py` 包含 `interrupt()`/`Command(resume=...)` 示例与直接函数测试，但构图未 `add_node`。

结论：**主图不支持 LangGraph checkpoint、进程级断点恢复或 HITL resume**。Redis PEL/重试恢复的是“任务重新执行”，不是“从图中断节点继续”。

## 8. 可以用于面试的精确调用链

### 同步 Fast

`aiops.py::aiops_diagnose()`
→ `aiops_service.py::stream_diagnose()`
→ `diagnosis_runner.py::run_diagnosis_graph()`
→ `graph.py::build_aiops_graph()`（进程级懒缓存）
→ `skill_router_node()`
→ `plan_node()`
→ `execute_node()`
→ `tool_filter.py::filter_tools_for_skill()`
→ `tool_runner.py::run_parallel_agent()`
→ `replan_node()`
→ `_convert_node_event()`
→ SSE。

### 异步 Deep

`webhook.py::alertmanager_webhook()`
→ `IncidentRepository.ingest_alertmanager_alert()`
→ `RedisIncidentQueue.enqueue_task()`
→ `DiagnosisWorker.handle_message()`
→ `audit.py::run_legacy_langgraph_with_audit()`
→ `diagnosis_runner.py::run_diagnosis_graph(mode=deep)`
→ `build_deep_graph()`
→ IncidentManager→CorrelationContext→EvidencePlan
→ Specialist fan-out/fan-in
→ EvidenceReducer→RCAJudge→RemediationPlanner→Report
→ audit/Task success
→ XACK。

注意：这条链的图确实运行 Deep，但由于 runner 不传 task/group IDs，Deep 的 DB 关联上下文未真正贯通；专业 Evidence 也未被 audit 分支逐条保存。

