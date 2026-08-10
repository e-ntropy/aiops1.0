# 双模式编排：设计解释

## 1. 为什么分 Fast 与 Deep

单一复杂图的问题是：简单故障也要支付多 Agent、长上下文和汇聚成本；复杂故障又可能因单 Agent 的单一路径形成确认偏差。Fast 用顺序计划和逐步 Replan 控制成本，Deep 用隔离 Specialist 扩大证据覆盖面。代价是两套 State/事件/权限/审计需要保持契约一致，当前 Deep 上下文与 Evidence 持久化未完全贯通就是这种复杂度的体现。

适用：问题复杂度和证据域差异明显、需要成本分层。若请求量小、工具少、问题同质，单图或单 Agent 更简单。

## 2. 为什么用 StateGraph

诊断不是一次 prompt→answer：它包含路由、计划、工具副作用边界、循环、条件终止、并发汇聚和可观测 State。StateGraph 把节点、边、Reducer 和停止条件显式化，比普通 Chain 更适合。代价是 State schema、Reducer 和并发语义更难维护。

替代方案：手写状态机控制更细、依赖更少，但 streaming、并行 join、可视化与未来 checkpoint 都要自行实现；AgentExecutor 适合单循环，不适合两张清晰业务状态图；CrewAI/AutoGen 更偏自治协作，本项目更强调确定性编排和证据边界。

## 3. 为什么 Fast 用 Plan-Execute-Replan

Planner 先把复杂问题拆成可执行步骤；Executor 每轮只处理 `plan[0]`，降低一次让模型执行多件事造成的混乱；Replanner 根据真实 Tool 结果决定继续、改计划、切 Skill 或收尾。收益是可观察、可纠错；成本是 LLM 往返和上下文累积，历史 E2E 中 Fast 的延迟/Token 高于 Deep，证明循环成本真实存在。

## 4. 为什么 Deep 用隔离 Specialist

日志、指标、基础设施和 Runbook 的数据源、Prompt、判断标准不同。隔离子 Agent 能减少工具菜单和上下文污染，只把压缩 Evidence 写回黑板；Reducer/RCA 统一处理冲突。代价是多个 LLM 调用、Evidence 摘要可能丢信息，以及真实数据源污染可能被多个 Agent共同放大。

## 5. 并行为什么安全

图级并行节点只追加 `evidences` 和 `transition_history`，它们在 `DeepDiagnosisState` 用 `operator.add` reducer；普通覆盖字段不由多个 Specialist 同时写。工具级并行只对 `ToolMeta.concurrency_safe` 的相邻调用做 `asyncio.gather`，不安全/写操作串行。这里的“安全”是编排层避免写冲突，不代表外部工具天然幂等。

## 6. 如何处理冲突与失败

Specialist 不互聊，不相互覆盖结论。失败被转为带 `error_type` 的 Evidence；Reducer 不把 error Evidence 当根因候选，但保留失败引用；RCAJudge只读结构化 summary，并在 LLM/解析失败时取确定性 top candidate。这样能保证出报告，但“保证出报告”不等于“保证根因正确”。

## 7. 有界执行的四层

1. Fast 业务步数：默认 5。
2. 最近三步完全重复：强制收尾。
3. LangGraph recursion limit：`max_steps*3+5`。
4. Tool Agent 的 max_iters；后台 Worker 另有整任务 timeout。

局限：重复检测不是语义相似；同步 Deep 没有 Worker 那层整图 timeout；Token budget 当前主要是运行后告警，而非中途停止。

## 8. 简历包装的推荐讲法

先说完整价值：“我设计了 Fast/Deep 两种执行策略，Fast 负责可重规划的低成本诊断，Deep 负责多证据域并行调查，并用预算与降级保证收敛。”被追问代码时再精确拆开两张图。这样既保留简历表达力，又能经受源码核验。

