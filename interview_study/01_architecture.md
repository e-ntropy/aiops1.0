# 整体架构恢复

## 1. 一句话架构

系统以 FastAPI 为接入层，以 `run_diagnosis_graph()` 为 Fast/Deep 统一编排入口；同步请求直接流式运行，异步请求先在 PostgreSQL 建立任务事实、再由 Redis Streams 投递给 Worker；Fast 图执行 Skill Router→Plan→逐步 Tool 调用→Replan，Deep 图执行关联上下文→规则取证计划→隔离 Specialist 并行→Evidence 汇聚→RCA→处置建议→报告；RAG 同时服务知识工具与 Chat；评测脚本在旁路复用 Router、检索和图入口。

## 2. 总体文字流程图

```text
用户问题 / Alertmanager firing alert
  -> FastAPI API
  -> [同步 SSE] 分布式并发准入
     OR
     [异步] PostgreSQL 创建/去重 DiagnosisTask -> Redis Streams XADD -> Worker
  -> diagnosis_runner.resolve_effective_mode()
  -> Fast Graph 或 Deep Graph
  -> Agent / Skill / Tool / RAG / MCP
  -> RuntimeEvent
  -> [同步] SSE 最终报告
     OR
     [异步] AgentRun / ToolCall / Evidence / Report 落 PostgreSQL -> XACK
  -> benchmark 旁路评测 Router / Retrieval / RAGAS / E2E
```

## 3. Fast Graph

```text
START
  |
  v
SkillRouter -- response(OOS) ----------------------> END
  |
  v
Planner -- plan[] --> Executor -- past_steps += 1 --> Replanner
                    ^                                  |
                    |          continue/new plan ------+
                    |
                    +-- Planner <-- pending_reroute ---+
                                                       |
                          response / empty / forced ----+--> END
```

构图证据：`app/agents/graph.py::build_aiops_graph()` L95-L135。它是一个 `StateGraph(PlanExecuteState)`，节点是 `skill_router`、`planner`、`executor`、`replanner`。条件边位于 L111-L130。

Fast 的“多 Agent”应谨慎解释：Router、Planner、Executor/Replanner 是职责分离的 LLM/执行节点，但不是四个并行专家；工具调用可以在单个 Executor 回合中按只读/并发安全属性并行（`tool_runner.py` L461-L487）。

## 4. Deep Graph

```text
START
  |
  v
IncidentManager
  |
  v
CorrelationContext
  |
  v
EvidencePlan (关键词规则决定派遣集合)
  |
  +----------+-------------+--------------+
  v          v             v              v
LogAgent  MetricAgent  InfraAgent    RunbookAgent
  +----------+-------------+--------------+
                     |
                     v
              EvidenceReducer
                     |
                     v
                 RCAJudge
                     |
                     v
          RemediationPlanner
                     |
                     v
                ReportAgent
                     |
                    END
```

构图证据：`app/diagnosis_graphs/deep_diagnosis_graph.py::build_deep_graph()` L894-L933。四条 `evidence_plan -> specialist -> evidence_reducer` 边是编译期静态 fan-out/fan-in；`_dispatch_guard()` L332-L356 让未被规则计划选中的节点快速跳过。因此“图上四路并行”已验证，“每次都实际运行四个专家”不成立。

## 5. 五条运行链

### 5.1 在线同步请求链

`app/main.py` 路由注册
→ `app/api/v1/aiops.py::aiops_diagnose()` L133
→ `app/services/aiops_service.py::stream_diagnose()` L23
→ `distributed_slot("manual_diagnosis", wait=False)` L35-L42
→ `app/orchestration/diagnosis_runner.py::run_diagnosis_graph()` L100
→ `graph.astream()` L182
→ `_convert_node_event()` L321
→ `EventSourceResponse`。

特点：同步 SSE 不落 `DiagnosisTask`/`AgentRun` 事实；并发满时立即返回 `concurrency_limited`，建议改用 submit 接口。

### 5.2 后台任务链

人工 submit 或 Alertmanager
→ `IncidentRepository` 在 PostgreSQL 创建/复用任务
→ `RedisIncidentQueue.enqueue_task()` (`XADD`, L128-L163)
→ `DiagnosisWorker.start()` 优先 `XAUTOCLAIM`，再 `XREADGROUP`
→ `mark_task_running()`
→ `distributed_slot("worker_diagnosis", wait=True)`
→ `run_legacy_langgraph_with_audit()`
→ `run_diagnosis_graph()`
→ 审计事件落 Evidence/ToolCall/AgentRun
→ `mark_task_succeeded()`
→ `XACK`。

事实边界：PostgreSQL 是任务/审计权威；Redis 是瞬时投递与协调。Schema 见 `app/db/postgres.py` L85-L269。

### 5.3 RAG 检索链

Markdown corpus/upload
→ `split_markdown()`：标题分段→parent→child，child metadata 带 `parent_id`/`parent_content`
→ `get_vector_store().add_documents()` 写 Milvus
→ `build_context(question)`
→ `advanced_search()`：Vector top-N → 可选 BM25+加权 RRF → 可选 Reranker
→ 按 `parent_id` 去重
→ 用 `parent_content`（最多 `rag_parent_max_chars`）拼给 LLM。

代码证据：`app/core/splitter.py` L116-L204；`app/core/vector_store.py` L116-L190；`app/core/hybrid_retriever.py` L241-L307；`app/rag/retrieval.py` L25-L101。

### 5.4 评测链

- Router：40 条 JSONL → 直接调用 `skill_router_node()` → 结构化 OOS/Skill 严格匹配 → accuracy。
- Retrieval：50 条 JSONL → `build_context()` → Gold source/chapter 分组匹配 → Hit/MRR/Recall。
- RAGAS/OpenEvals：50 条 QA → 检索→生成→RAGAS 四指标 + 两个 LLM judge 指标。
- Diagnosis E2E：10 条合成事故 × Fast/Deep → 图执行→根因机制组/证据引用/延迟/Token。

这四层由三个脚本实现，并非一个统一测试框架：`run_skill_router_benchmark.py`、`run_benchmark.py`、`run_diagnosis_benchmark.py`。

### 5.5 错误恢复链

```text
节点异常
  -> Fast Router/Planner/Replanner: 规则/模板报告兜底
  -> Fast Tool: ToolMessage 失败文本，继续循环
  -> Deep Specialist: error Evidence，其他 Agent/Reducer/RCA/Report 继续
  -> RCA LLM: 取 Reducer 排名第一候选

图级异常
  -> diagnosis_runner 发 error
  -> 同步 SSE 返回错误
  -> Worker 将 AgentRun 标 failed/cancelled
  -> 未达 max_attempts: task->pending, XADD 新消息, ACK 旧消息
  -> 达上限/坏消息: XADD DLQ, ACK 原消息

Worker 崩溃且未 ACK
  -> 消息留 PEL
  -> 存活 Worker 以 XAUTOCLAIM 回收
```

## 6. 存储与一致性边界

| 组件 | 权责 | 关键风险 |
| --- | --- | --- |
| PostgreSQL | Alert、IncidentGroup、Incident、DiagnosisTask、Evidence、AgentRun、ToolCall、Approval | Schema 在应用启动时 `CREATE/ALTER IF NOT EXISTS`，无正式迁移框架 |
| Redis Streams | 投递、Consumer Group、PEL、优先级流、DLQ | at-least-once，存在重复消费；重试的 DB 更新/XADD/ACK 不是事务 |
| Redis Key | Worker 心跳、分布式并发槽、限流/会话 | 瞬时状态，不应当事实来源 |
| Milvus | child chunk 与 metadata/vector | BM25 从 Milvus 全量拉取且上限 16384，规模化后不完整 |
| 文件 Wiki | 经验页 | 可能包含事故信息，默认不应公开提交 |

## 7. 关键架构事实与限制

1. Fast/Deep 是两个独立 `StateGraph`，不是单图的两个条件分支；选择发生在图外 `resolve_effective_mode()`。
2. Alertmanager 以严重度/告警数量确定请求模式（`webhook.py::_diagnosis_mode_for()` L81-L88）；人工接口由请求显式传 mode。系统没有另一个 LLM 自动选择 Fast/Deep。
3. Deep 不是动态 Replan 图；它是确定性阶段编排 + 规则 EvidencePlan + 并行调查节点。
4. 两张图均以 `compile()` 无参数编译，主链没有 checkpointer/thread_id/`Command(resume=...)`。`hitl.py` 有示例代码，但未接入构图，因此【未在当前主链定位到 checkpoint/恢复执行】。
5. Deep Agent 当前 `decisions=None`，未统一接 Fast 的 PermissionMode；InfraAgent 自己用硬编码只读白名单降低风险，但这不是统一权限闭环。
6. `run_diagnosis_graph()` 的 graph input 只传 input/mode/signature（L171-L177），Worker 没把 `task_id/incident_group_id/incident_id` 传入 Deep State。因此 Deep 的 IncidentManager/CorrelationContext 在当前主链会走“manual/no group”分支，即使来源是 Worker。这是【部分实现】。
7. Worker 审计包装只持久化 `tool_call`、`step_complete`、`report` 等事件（`audit.py` L138-L185）；Deep `_convert_node_event()` 产生的 `evidence` 事件没有对应持久化分支。因此专业 Evidence 在图内真实存在，但【未逐条落 PostgreSQL Evidence】。
8. Deep 没有“整体异常自动切 Fast”；Deep 开关关闭时才在执行前回落 Fast。运行中图级异常会发 error，由 Worker 重试/DLQ。

