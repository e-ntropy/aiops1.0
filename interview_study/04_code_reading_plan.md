# 从入门到面试的源码阅读路线

## 使用方法

每个阶段都按“入口→State→节点→下游→失败路径→证据复述”阅读。不要先背概念。完成标准不是“看过文件”，而是能不看稿画出调用链、说出字段变化、指出至少一个真实限制。

涉及远端模型、Milvus 重建、PostgreSQL 写入或真实诊断的命令默认不运行；先用静态命令和评分单测。本文中的运行命令需在正确虚拟环境、服务和凭证已确认后使用。

## 阶段 1：项目启动与整体链路

### 阅读文件

1. `AGENTS.md`
2. `README.md`
3. `app/main.py`
4. `app/api/v1/aiops.py`
5. `app/services/aiops_service.py`
6. `app/orchestration/diagnosis_runner.py`
7. `docker-compose.yml`

### 重点函数

- `main.py::lifespan()`、路由注册
- `aiops.py::aiops_diagnose()`、`submit_diagnose()`
- `aiops_service.py::stream_diagnose()`
- `diagnosis_runner.py::resolve_effective_mode()`、`run_diagnosis_graph()`、`_convert_node_event()`

### 应该能回答

- 同步 SSE 与异步 submit 为什么并存？
- Fast/Deep 在哪里选，选择依据是什么？
- API、service、orchestration 为什么分层？
- 完整 Compose 与 `run.ps1` 有什么差别？

### 可运行命令

```powershell
rg -n "include_router|aiops_diagnose|submit_diagnose|stream_diagnose|run_diagnosis_graph" app
docker compose config --quiet
docker compose config --services
```

服务已明确启动后：

```powershell
curl.exe -fsS http://localhost:9900/api/v1/health/ready
```

### 完成标准

3 分钟内从 HTTP 入口讲到 graph.astream，再讲清同步/异步分叉；不把 Redis 队列错误地放进同步 SSE 必经链。

## 阶段 2：LangGraph State 和节点

### 阅读文件

- `app/agents/state.py`
- `app/agents/state_deep.py`
- `app/agents/graph.py`
- `app/diagnosis_graphs/deep_diagnosis_graph.py`
- `app/runtime/transitions.py`

### 重点符号

- `PlanExecuteState`、`DeepDiagnosisState`
- `Annotated[..., operator.add]`
- `build_aiops_graph()`、`build_deep_graph()`
- `route_after_skill()`、`should_end()`

### 应该能回答

- 覆盖 reducer 和 `operator.add` reducer 有什么区别？
- 为什么 `past_steps/evidences/transition_history` 要累加？
- 两张图的节点、边、终止条件分别是什么？
- 并发 Agent 如何安全汇合？

### 可运行命令

```powershell
rg -n "StateGraph|add_node|add_edge|add_conditional_edges|Annotated|operator.add|compile" app/agents app/diagnosis_graphs
```

### 完成标准

能手画两张图；对每个 State 字段说出写入节点与读取节点；明确“无 checkpointer”。

## 阶段 3：Fast/Deep 双图

### 阅读文件

Fast：

- `app/agents/skill_router.py`
- `app/agents/planner.py`
- `app/agents/executor.py`
- `app/agents/replanner.py`
- `app/runtime/agent_harness.py`

Deep：

- `app/diagnosis_graphs/deep_diagnosis_graph.py`
- `app/agents/log_agent.py`
- `app/agents/metric_agent.py`
- `app/agents/infra_agent.py`
- `app/agents/runbook_agent.py`

### 重点函数

- Fast：`skill_router_node()`、`plan_node()`、`execute_node()`、`replan_node()`、`evaluate_replanner_pre_llm()`
- Deep：`evidence_plan_node()`、`_dispatch_guard()`、四个 `run_*_agent()`、`evidence_reducer_node()`、`rca_judge_node()`、`report_node()`

### 应该能回答

- 为什么不把 Fast/Deep 合成一个大图？
- Fast 的 Replan 如何覆盖 plan、保留 past_steps？
- 重复检测算法能检测什么、漏掉什么？
- Deep 是否每次运行四 Agent？并行冲突如何处理？
- Specialist 失败为何用 error Evidence 而不是抛出？
- Deep 为什么当前不能宣称比 Fast 准确？

### 可运行命令

```powershell
rg -n "max_agent_steps|_has_repeated_steps|_validate_reroute|SPECIALISTS|_dispatch_guard|evidence_reducer_node|rca_judge_node" app
```

在无外部调用前提下可做 import/编译检查；不要把 import 成功说成图运行成功。

### 完成标准

能用 5 分钟对比两个图的目标、状态、控制流、成本和失败策略，并主动说出 Deep 无 Replan、无主链 checkpoint。

## 阶段 4：Skill、Playbook 和工具权限

### 阅读文件

- `app/skills/models.py`
- `app/skills/loader.py`
- `app/skills/registry.py`
- `app/skills/definitions/*/SKILL.md`
- `app/runtime/tool_filter.py`
- `app/runtime/permissions.py`
- `app/runtime/tool_runner.py`
- `app/runtime/approvals.py`
- `app/tools/meta.py`

### 重点函数

- `Skill.to_router_card()`
- `load_skill_from_file()`、`get_skill_registry()`
- `filter_tools_for_skill()`、`evaluate_permission()`
- `run_parallel_agent()`、`partition_tool_calls()`
- `ApprovalRepository.create_request()/wait_for_decision()`

### 应该能回答

- Skill/Playbook/Tool/Agent 各是什么？
- “渐进式披露”是 prompt 披露还是磁盘懒加载？
- deny 为什么既不暴露给模型，又在执行时防伪造？
- `read_only/normal/ask_destructive/bypass` 的边界？
- 参数级权限是否实现？Deep 是否复用同一权限链？

### 可运行命令

```powershell
rg -n "to_router_card|playbook|allowed_tools|filter_tools_for_skill|evaluate_permission|behavior == \"ask\"" app
```

### 完成标准

能画出“metadata→selected Skill→Playbook→Tool visibility→execution check→approval”的完整链；不会把 Prompt 约束当权限实现。

## 阶段 5：Hybrid RAG

### 阅读文件

- `app/core/splitter.py`
- `scripts/ingest_kb_corpus.py`
- `app/core/embedding.py`
- `app/core/vector_store.py`
- `app/core/hybrid_retriever.py`
- `app/core/reranker.py`
- `app/rag/retrieval.py`
- `app/tools/knowledge_tool.py`
- `app/services/rag_service.py`

### 重点函数

- `split_markdown()`
- `get_vector_store()`、`safe_similarity_search()`、`advanced_search()`
- `_BM25Index.build/search()`、`refresh_bm25_index()`、`hybrid_search()`
- `build_context()`

### 应该能回答

- child 与 parent 实际存在哪里？“回溯”是否另查父表？
- 加权 RRF 的公式、rank 常数和权重作用？
- BM25 如何构建，Milvus 不可用时如何降级？
- Parent 去重发生在 Rerank 前还是后？
- 0.800→0.860 A/B 隔离了哪个变量？

### 可运行命令

```powershell
python scripts/ingest_kb_corpus.py --dry-run
python benchmark/run_benchmark.py retrieval --k 3 --limit 3
```

第二条会访问已配置 Milvus/Embedding；服务和数据未确认时不要运行。绝不在未备份/未批准时加 `--reset`。

### 完成标准

能从 Markdown 输入讲到 child vector，再讲到 parent context；手算一个两路 RRF 示例；明确历史 A/B 不能证明 Parent-Child 独立贡献。

## 阶段 6：Redis Streams 和 PostgreSQL

### 阅读文件

- `app/db/postgres.py`
- `app/incidents/repository.py`
- `app/queue/redis_streams.py`
- `app/diagnosis_worker.py`
- `app/orchestration/audit.py`
- `app/orchestration/repository.py`
- `app/evidence/repository.py`
- `app/core/distributed_limiter.py`

### 重点函数

- `RedisIncidentQueue.ensure_group/enqueue_task/read_tasks/claim_stale_tasks/ack/dead_letter`
- `DiagnosisWorker.handle_message/_handle_failure`
- `run_legacy_langgraph_with_audit()`
- task `mark_*` 方法与 active dedup unique index

### 应该能回答

- XADD/XREADGROUP/XACK/XAUTOCLAIM 对应哪段代码？
- PEL、at-least-once、重复消费分别是什么？
- ACK 前后崩溃会怎样？Postgres/Redis 如何可能不一致？
- 当前“重试”缺少哪些生产要素？
- 为什么 Worker 任务恢复不等于 LangGraph checkpoint？

### 可运行命令

```powershell
rg -n "xadd|xreadgroup|xack|xautoclaim|dead_letter|mark_task_|CREATE TABLE|CREATE UNIQUE INDEX" app
docker compose config --quiet
```

完整队列测试只在隔离环境、允许写测试数据时按 `docs/CONCURRENCY_TEST_GUIDE.md` 执行。

### 完成标准

能列出至少四个崩溃窗口并给出现实现行为；不宣称 exactly-once、事务消息或指数退避。

## 阶段 7：评测体系和指标

### 阅读文件

- `benchmark/README.md`
- `benchmark/run_skill_router_benchmark.py`
- `benchmark/run_benchmark.py`
- `benchmark/run_diagnosis_benchmark.py`
- 四个 JSONL 数据集
- `benchmark/reports/FINAL_METRICS_REPORT.md`
- 被引用的原始 JSON 报告

### 重点函数

- Router：`classify_router_outcome()`、`score_router_result()`、`run_router_benchmark()`
- Retrieval：`is_relevant()`、`score_hits()`、`run_retrieval()`
- RAGAS：`run_single_ragas()`、`run_openevals()`、`run_ragas()`
- E2E：`group_coverage()`、`score_state()`、`summarize()`

### 应该能回答

- Hit@3、MRR@3、Recall@3 如何逐样本再求均值？
- OOS 为什么必须看结构化 transition？
- Faithfulness/Context Recall/Groundedness/Helpfulness 的 judge 与聚合方式？
- 160 的组成是什么？为什么不能说成 160 次完整 E2E？
- 模型、索引、Gold、checkpoint、失败样本如何影响可信度？

### 可运行命令

```powershell
python -m unittest benchmark.test_skill_router_scoring benchmark.test_diagnosis_benchmark
python benchmark/run_benchmark.py retrieval --k 3
```

RAGAS/Router/E2E 可能调用付费模型，需先确认凭证、成本、数据与服务。

### 完成标准

能手算 Hit/MRR/Recall；能从 JSON 报告指出 rows、模型、开关和错误数；主动区分历史结果与当前复现。

## 阶段 8：错误处理和系统边界

### 阅读文件

- `app/exceptions.py`
- `app/api/middleware.py`
- `app/runtime/agent_harness.py`
- `app/core/rate_limiter.py`
- `app/core/distributed_limiter.py`
- `app/agents/hitl.py`
- `docs/ARCHITECTURE.md` 的已知限制

### 重点检查

- 每类异常由谁捕获、转成何种状态/事件？
- Redis 限流 fail-open 与队列 Redis 故障有什么不同？
- HITL/多 judge/设计注释是否接入主图？
- 同步与后台链路的 timeout、审计和持久化是否一致？

### 可运行命令

```powershell
rg -n "except Exception|fallback|fail-open|timeout|checkpointer|interrupt|multi_judge" app docs
```

### 完成标准

形成“已实现/部分实现/未接线/仅实验”的口头清单；面对质疑先给代码路径，再给限制，而不是防御性包装。

## 阶段 9：模拟面试

### 练习顺序

1. 1 分钟项目介绍：问题、两图、可靠运行时、RAG、指标、边界。
2. 5 分钟白板：同步/异步总架构与两张 StateGraph。
3. 逐条简历深挖：每条按“结论→调用链→设计原因→失败场景→指标→限制”。
4. 真实性压力测试：随机给出一个数字，必须定位数据集、脚本、JSON 与公式。
5. 反向质疑：解释 Deep Top-1 0%、为何仍保留 Deep 架构，以及如何用事故隔离 fixture 改善评测。

### 应该能回答

- “这只是多个函数，为什么叫 Multi-Agent？”
- “Redis Streams 真的不会丢吗？”
- “为什么 50 条就说提升？”
- “Deep 为什么比 Fast 指标差？”
- “哪三点是你现在绝不会写进简历的？”

### 可运行命令

```powershell
rg -n "^## |^### " interview_study/*.md
```

第二阶段完成后再对四份 `04_interview_questions_30.md` 做精确计数。

### 完成标准

- 不看代码能准确说出至少 20 个关键文件/函数的职责。
- 任一简历数字能在 30 秒内给出“数据→脚本→公式→报告→限制”。
- 能主动承认 Deep 上下文/持久化/权限/checkpoint 四个缺口，同时提出具体补强位置。
- 回答既不夸大，也不把真实实现贬成“只是 Demo”：用已接线事实证明价值，用评测反例证明工程诚实。

## 建议学习节奏

| 天 | 阶段 | 交付物 |
| --- | --- | --- |
| 1 | 1-2 | 总链路图 + State 字段表 |
| 2 | 3 | Fast/Deep 对照讲稿 |
| 3 | 4 | Skill/权限威胁模型 |
| 4 | 5 | RAG 流程 + 指标手算 |
| 5 | 6 | 队列失败时序表 |
| 6 | 7-8 | 指标证据卡 + 风险清单 |
| 7 | 9 | 两轮录音模拟面试与复盘 |

