# Multi-Agent AIOps 项目简历与面试手册

> 适用岗位：大模型应用开发、Agent 工程、RAG 工程、AI 平台开发实习。项目为个人公开参考实现，
> 面试时应表达为“设计、实现并在版本化数据集上验证”，不虚构真实生产用户、团队规模或线上收益。

## 1. 项目定位

**项目名称：Multi-Agent AIOps 智能诊断与评测平台**

运维人员处理故障时，需要在知识文档、指标、日志、系统进程和 Runbook 之间反复切换。普通 RAG
只能解释知识，无法确认真实系统状态；自由 ReAct Agent 又容易选错工具、混淆知识与现场事实，且
缺少人工确认、恢复验证和经验更新。因此项目构建了一条统一 Agent 执行链：

```text
知识咨询 / 告警
  → Query 理解、改写与任务拆分
  → Target Scope 确认
  → Capability / Skill / Tool 渐进式规划
  → RAG 或只读工具取证
  → Evidence Gate 驱动的自适应故障诊断
  → 结构化 Evidence、RCA 与只读处置计划
  → 人工确认
  → 同 Scope 重新取证并验证恢复
  → 事务关闭事故
  → Memory、画像、成败经验与隔离评测样本沉淀
```

技术栈：Python、FastAPI/SSE、LangGraph/LangChain、PostgreSQL、Redis Streams、Milvus、
BM25/RRF、MCP、Docker Compose、RAGAS/OpenEvals。

## 2. 一页简历推荐版

**Multi-Agent AIOps 智能诊断与评测平台｜个人项目**

**项目背景：** 面向 SRE/OnCall 中知识查询、现场取证、故障诊断与恢复验证彼此割裂的问题，设计
统一的多智能体诊断平台，通过 RAG 与只读 MCP 工具形成证据链，并以 HITL、持久化事故生命周期和
分层 Benchmark 构建“咨询—诊断—验证—沉淀—评测”的闭环。

### 2.1 统一 Agent 与事故闭环（STAR）

- **S：** 单轮回答无法承载 Query 澄清、工具取证、根因确认和恢复验证，多端提交还可能覆盖状态。
- **T：** 建立一条可恢复、可审计、并发安全的统一执行链，并让事故关闭以事实而非模型文字为准。
- **A：** 设计服务端 `WorkflowState`，用 Postgres 保存 Run/Event/Decision/Evidence；以 `revision`
  乐观锁和执行 Lease 防止并发覆盖；串联根因确认、只读计划确认、同 Scope 新快照验证和事务关闭。
- **R：** 32 条 Query 与 9 条 Lifecycle 契约在版本化门禁上 Exact Match 均为 100%；87 个本地
  确定性测试通过，覆盖状态转换、409 冲突、Lease、关闭事务和失败路径。

**简历压缩句：** 设计 Query → Scope → Capability → Evidence → RCA → HITL → Recovery → Learning
统一 Agent 链路，以 Postgres 事实库、revision CAS 和执行 Lease 管理服务端状态；用 41 条契约样本
验证 Query/Scope/生命周期，当前数据集 Exact Match 100%。

### 2.2 Hybrid RAG 与知识证据治理（STAR）

- **S：** 纯向量检索容易遗漏错误码和指标名，小分块命中后又缺少完整 SOP 上下文；知识内容还可能
  被错误当成当前机器状态。
- **T：** 同时提升精确 Token 召回和上下文完整性，并严格隔离 Reference 与 Observed Evidence。
- **A：** 实现 Parent-Child 切分，使用 Milvus Dense + BM25 Sparse 召回并通过 RRF 融合，保留可选
  Rerank；检索结果只写 `reference` Evidence，现场结论必须来自绑定 Scope 的受信 ToolCall。
- **R：** 50 条检索集上 Hybrid hit@3 为 0.860，相比 Dense 0.800 提升 6.0pp，MRR@3 从 0.710
  提升到 0.777；50 条生成评测 faithfulness 0.869、answer relevancy 0.885。

**简历压缩句：** 设计 Parent-Child + Milvus/BM25/RRF Hybrid RAG，并区分知识 Reference 与现场
Observed Evidence；50 条检索集 hit@3 由 0.800 提升至 0.860，MRR@3 提升 0.067。

### 2.3 Skill/Tool 渐进式披露与状态机（STAR）

- **S：** 一次性向模型暴露全部 Playbook 与工具会造成上下文干扰，也可能让只读诊断看到重启等
  副作用能力。
- **T：** 在不扩大权限的前提下覆盖常见故障域，并让每个状态转换可判定、可回归。
- **A：** Capability 阶段只披露能力元数据，命中后再加载 7 个领域 Skill 的 Playbook 和 Tool
  allowlist；Tool 继续经过 `ToolMeta`、PermissionMode、Guardrail、Budget 与审批校验；状态机约束
  Phase、Scope、Evidence 和合法转换。
- **R：** 7 个内置 Skill 的默认 ToolMeta 只读断言全部通过；32 条 Query 集覆盖多意图、模糊 Scope、
  Prompt Injection 与越权请求，Capability/Scope/Safety 在该数据集均为 100%。

**简历压缩句：** 实现 Capability → Skill 元数据 → Playbook/Tool 按需加载的渐进式披露，结合
ToolMeta/Permission/Guardrail 与 Workflow 状态机收敛工具范围；7 个领域 Skill 默认工具全部通过
只读契约校验。

### 2.4 Memory、画像与经验更新（STAR）

- **S：** 多轮使用后，候选结论、失败对话和真实机器数据可能相互污染，使后续召回持续劣化。
- **T：** 明确何时读取、何时写入、何时晋升或失效，使 Memory 可追溯且不能绕过人工确认。
- **A：** 在 Postgres 划分 Session、Candidate、Verified Knowledge、Entity Profile 和 Success/Failure
  Experience；创建 Run 时按 Session/Incident/Service/Scope 召回，只允许 `status=verified` 的长期
 知识进入诊断；到期记录自动标为 expired，Candidate 在事故确认恢复和脱敏关闭后才被 Verified
  Memory 替代。
- **R：** 9 条生命周期样本覆盖确认、纠正、拒绝、恢复失败、脱敏失败与 Memory 晋升并全部通过；
  单元测试验证 Candidate 不进入长期召回，诊断缓存按 Session Key 隔离。

**简历压缩句：** 构建 Session/Candidate/Verified Memory、实体画像和成败经验分层，按 Scope 召回并
以 HITL + 恢复验证 + 脱敏关闭作为知识晋升门禁；生命周期评测 9/9，候选记忆与跨 Session 报告污染
均由回归测试阻断。

### 2.5 Agent Harness、兜底与 HITL（STAR）

- **S：** 工具超时、格式异常、Provider 失败或重复消费会让 Agent 无限重试、伪造成功或留下悬挂
  AgentRun；高风险建议若只靠 Prompt 约束也容易越权。
- **T：** 将预算、重试、降级、审计和人工确认变成代码层不变量。
- **A：** Harness 统一步骤/Token/时间预算和模型调用；工具仅对可重试错误有限重试，耗尽后切换
  数据源、输出证据缺口或 fail-closed；Observed Evidence 强制 Scope + ToolCall ID；异常/取消时关闭
  AgentRun 并释放 Lease；优化助手只输出 observe/verify/recommendation，禁止自动修改系统。
- **R：** 16 条诊断 Fixture 覆盖正常、反例、未知、数据源全挂、畸形 Evidence 和协作失败，Phase、
  Evidence Type、Isolation 与 Exact Match 均为 16/16；历史并发验证中 8/8 Worker 任务完成，峰值
  执行槽保持 2/2。

**简历压缩句：** 通过 Harness 统一预算、有限重试、降级和审计，以 Scope/ToolCall 绑定阻断伪现场
Evidence，并用 HITL 控制根因/计划/关闭；16 条故障夹具的流程、隔离与 Exact Match 均为 100%。

### 2.6 分层评测体系（STAR）

- **S：** “能生成报告”不能证明路由正确、证据属于目标对象、答案有引用或事故流程安全。
- **T：** 建立从确定性契约到检索、生成、诊断和并发的分层评测，并避免真实机器与 Mock 数据互相污染。
- **A：** 自建 207 条版本化样本：32 Query、9 Lifecycle、40 Skill Router、50 Retrieval、50 RAG QA、
  16 Diagnosis Fixture、10 Diagnosis E2E；固定数据集指纹、环境元数据和 Release Gate，事故关闭样本
  写入隔离区，审核后才能进入正式集合。
- **R：** 本轮可离线复现的 Workflow 41/41、Diagnosis Fixture 16/16 均通过 Gate；检索和 RAG 指标
  分层保存，避免用少量 Smoke Test 外推“零幻觉”或生产准确率。

**简历压缩句：** 构建 207 条分层 Benchmark，覆盖 Query、生命周期、路由、检索、RAG、正反例诊断
与 E2E；通过数据集指纹、隔离样本和 Release Gate 防止数据漂移与真实宿主机污染，当前确定性
Workflow 41/41、Fixture 16/16 通过。

## 3. 四点精简版

- 设计服务端统一 Workflow，以 Postgres Run/Event/Decision/Evidence、revision CAS 和 Lease 串联
  Query、真实取证、RCA、HITL、恢复验证和事务关闭；41 条契约样本 Exact Match 100%。
- 实现 Parent-Child + Milvus/BM25/RRF Hybrid RAG，严格区分 Reference/Observed Evidence；50 条
  检索集 hit@3 从 0.800 提升至 0.860，MRR@3 从 0.710 提升至 0.777。
- 以 Capability/Skill/Tool 渐进式披露、状态机和 Harness 统一预算、权限、有限重试与降级；7 个领域
  Skill 默认工具均为只读，16 条诊断 Fixture 的流程与隔离检查 100% 通过。
- 构建 Postgres Memory/画像/成败经验生命周期与 207 条分层 Benchmark，Verified Memory 仅在人工
  确认、恢复验证和脱敏关闭后晋升，事故样本进入隔离评测区。

## 4. 面试开场

### 30 秒版本

> 我做了一个面向 SRE/OnCall 的多智能体 AIOps 诊断与评测平台。它不是单轮运维问答，而是先理解
> Query 和 Scope，再通过 RAG 或只读工具取证；Evidence Gate 决定是否启动 Metric、Log、Infra、
> Runbook 专业 Agent，之后经过人工确认、同 Scope 恢复验证和事务关闭，把可信经验沉淀到 Postgres。
> 我还构建了 207 条分层 Benchmark；当前确定性 Workflow 41/41、诊断 Fixture 16/16 通过。

### 两分钟版本

> 这个项目解决两个实际问题。第一，运维知识和现场状态容易混淆：知识库说“CPU 高可能是什么”并
> 不能证明当前 CPU 高；第二，诊断不是一次生成，而是 Query 澄清、工具取证、根因确认、恢复验证和
> 经验更新的生命周期。因此我把这些阶段统一到服务端 WorkflowState，Postgres 保存 Run、Event、
> Evidence 和人工决定，revision CAS 与 Lease 处理并发和重复执行。
>
> 故障诊断先做最小证据采集，再由确定性 Evidence Gate 判断是否启动专业 Agent。各专业 Agent 不
> 共享私有推理，只返回结构化 Evidence；Observed Evidence 必须绑定已验证 Scope 和 ToolCall ID。
> 工具还要经过 Skill allowlist、ToolMeta、PermissionMode、Guardrail 和预算，高风险操作不进入当前
> 只读链路。事故必须人工确认根因和计划，并用同一 Scope 的新快照验证恢复，关闭事务才会生成
> Verified Memory、画像、经验和隔离评测样本。
>
> 评测上我没有只测几个 Demo，而是分成 Query、生命周期、Router、Retrieval、RAG、Diagnosis
> Fixture 和 E2E，共 207 条。Hybrid RAG 在 50 条检索集上 hit@3 从 0.800 提升到 0.860；当前离线
> Workflow 41/41、Fixture 16/16 通过。对外我把这些描述为参考实现指标，不外推真实线上 MTTR。

## 5. 高频追问

### 为什么需要多 Agent？

不是为了增加 Agent 数量，而是隔离 Metric、Log、Infra 和 Runbook 的工具上下文与失败域。简单问题
在初步证据阶段结束；只有证据不足或冲突时才启动专业协作。每个 Agent 只回传压缩 Evidence，避免
完整对话互相锚定和上下文膨胀。

### 为什么不用客户端保存完整 State？

客户端 State 可被篡改，多标签和重试会产生 Lost Update，也无法在进程重启后恢复。现在客户端只
提交 `run_id + expected_revision`，服务端以 Postgres 快照为事实源；版本冲突返回 409，执行 Lease
阻止同一 Run 并行运行。

### 工具调用失败怎么兜底？

先区分 retryable 与 non-retryable。超时、连接类错误在预算内有限重试；参数错误、权限拒绝不重试。
耗尽后按能力切换数据源、降级为当前快照分析、输出证据缺口或 fail-closed。每次失败写 FailureRecord；
异常或取消时尽力关闭 AgentRun、释放 Lease，避免只在日志里留下错误。

### 如何防止 Memory 越用越差？

Session、Candidate、Verified Knowledge 和失败经验分层保存；长期诊断只召回 verified、未过期、未被
supersede 且 Scope/Service 匹配的记录。普通模型输出不能晋升，必须经过人工确认、事实恢复验证、
脱敏和关闭事务。到期记录变为 expired，冲突记录由 superseded_by 关联，保留审计但退出召回。

### 为什么评测样本不能立即进入 RAG？

事故关闭样本包含刚得到的答案，如果立即参与召回会产生标签泄漏，也可能把真实机器细节带入公共
语料。因此样本先进入 `quarantine/isolated`，经脱敏、去重、Gold 审核和事故家族切分后才能进入正式
Benchmark；它不进入在线 Memory 查询。

### 这个项目距离真实生产还差什么？

主要是身份认证与租户隔离、远程 Target Agent/凭据管理、真实 Prometheus/Loki/Trace Provider、
Postgres/Redis 故障注入、Python 3.12 CI、灰度发布和长期真实事故 Gold。面试时可以说明这些边界，
但核心设计与确定性契约已经通过本地代码和版本化评测验证。

## 6. 指标口径

| 指标 | 规模 | 结果 | 口径 |
| --- | ---: | ---: | --- |
| Query Contract | 32 | 100% | Intent/Capability/Scope/Safety Exact Match |
| Lifecycle Contract | 9 | 100% | HITL/Verification/Closure/Memory Exact Match |
| Diagnosis Fixture | 16 | 100% | Phase/Mode/Fault/Evidence/Isolation/Exact Match |
| Retrieval | 50 | hit@3 0.860 | Hybrid；Dense 对照 0.800 |
| RAG QA | 50 | Faithfulness 0.869 | 历史固定 Provider 评测报告 |
| Skill Router | 40 | 0.750 | 历史固定数据集，不包装为生产准确率 |
| 本地 unittest | 87 | 全部通过 | 确定性与 Mock；不代表外部服务集成 |
| 版本化 Benchmark | 207 | 7 个数据层 | 不把不同层的样本简单相加为准确率 |

## 7. 表述边界

可以说：设计并实现统一闭环、Postgres 事实模型、CAS/Lease、只读 Agent Harness、Memory 晋升、
版本化 Benchmark，并在明确的数据集上取得上述结果。

不要说：已经生产部署、零幻觉、线上 MTTR 降低、百万告警、完整测试覆盖、100 个工具或所有运维场景
准确率 100%。这些说法没有当前代码和报告支撑，也经不起面试追问。
