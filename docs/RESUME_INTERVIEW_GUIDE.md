# Multi-Agent AIOps 项目简历与面试手册

> 适用岗位：Agent/LLM 应用开发、RAG 工程、AI 平台开发、AIOps/SRE 平台开发。  
> 表述原则：以真实代码和评测报告为依据，按个人项目负责人视角讲解，不冒充生产经历。  
> 深入学习见：[项目技术学习手册](PROJECT_TECHNICAL_LEARNING.md)。
> 今日重构闭环见：[2026-08-10 重构方案、流程与成果](REFACTOR_20260810_SUMMARY.md)。

## 1. 项目在简历中的定位

### 1.1 推荐项目名称

**Multi-Agent AIOps 智能诊断与评测平台**

### 1.2 项目背景

传统 OnCall 排障依赖人工在指标、日志、容器状态和 SOP 之间反复切换；普通 RAG 问答只能回答
“文档怎么说”，不能动态调用工具、根据证据调整计划，也缺乏副作用控制和最终诊断指标。

项目面向 SRE/OnCall 场景，将用户描述或 Alertmanager 告警转化为结构化 Query、Scope 与 Capability，
通过 Skill Router 选择 Playbook，使用 LangGraph 执行 Evidence Gate 驱动的自适应诊断，结合 RAG 与
MCP 工具收集证据，并通过人工确认、恢复验证、Memory 和分层评测形成完整事故生命周期。

项目工作的核心是完成从告警接入、任务调度、Agent 诊断、RAG/工具取证，到人工确认、恢复验证、
事故关闭和经验评测沉淀的完整链路，并通过分层 Benchmark 验证流程、安全、证据和最终根因质量。

### 1.3 技术栈

```text
Python 3.11/3.12
FastAPI / Uvicorn / SSE
LangGraph / LangChain
DeepSeek API
Milvus / sentence-transformers / BM25 / RRF / RAGAS / OpenEvals
Redis Streams / Consumer Group
PostgreSQL
MCP / psutil / Docker tools
Docker Compose
```

### 1.4 使用这份材料的前提

以下内容以“项目负责人、能够解释整体设计与关键实现”的视角组织。使用前至少应做到：

1. 能从 API 入口沿代码解释 Fast/Deep 两条链路。
2. 能画出 Redis Streams、Worker、Postgres 和执行槽的关系。
3. 能手算 RRF、MRR、group recall 和 citation correctness。
4. 能解释每个安全边界为什么不能只依赖 Prompt。
5. 能解释历史 Deep 数据源污染如何被离线夹具门禁约束，以及真实远程 Evidence Provider 仍缺什么。

简历可以突出你对完整项目的设计、实现和验证能力，但不要虚构生产用户、线上事故、团队规模、
业务收入或不存在的性能数据。对确实不是亲自完成的代码，应先通过复现、修改和讲解把它转化为
可验证的个人能力。

## 2. 可直接放进简历的项目经历

### 2.1 一页简历推荐版（约占页面 20%–30%）

**Multi-Agent AIOps 智能诊断与评测平台｜个人项目**

**技术栈：** Python、LangGraph/LangChain、FastAPI/SSE、Milvus、BM25/RRF、
Redis Streams、PostgreSQL、MCP、RAGAS、Docker

**项目背景与目标：** 针对 SRE/OnCall 需人工跨指标、日志和 Runbook 排障，且普通 RAG 无法查询
现场状态、验证恢复的问题，设计从 Query 理解、真实取证、故障诊断到人工关闭和持续评测的 AIOps
智能诊断平台。

**主要工作与成效：**

- **统一业务闭环：** 针对知识问答、现场查询、Fast/Deep 诊断和事故关闭状态割裂，设计
  Query Understanding → Scope → Capability → Evidence → Outcome 统一契约；将 Fast/Deep
  合并为 Evidence Gate 驱动的自适应诊断，并补齐人工根因确认、同 Scope 恢复验证、脱敏关闭及
  Memory/Evaluation 闭环。
- **Evidence 安全治理：** 针对 E2E 中真实宿主机 CPU/内存污染合成事故，为 Observed Evidence
  建立 Scope、受信 ToolCall ID、fixture 三元绑定与畸形输入 fail-closed；构建 16 条正常/边界、
  正例/反例/未知、简单/复杂夹具，当前 Isolation 与 Exact Match 均为 16/16。
- **Skill 与权限收敛：** 采用“Skill 元数据路由 → Playbook/工具按需加载”，将数据库/缓存、应用
  运行时、消息队列专科 Skill 与 Runbook 纳入诊断，内置 Skill 从 4 个扩展至 7 个；通过 ToolMeta
  测试发现并移除默认 `docker_restart` 权限，所有内置 Skill 默认工具均为只读。
- **Hybrid RAG 提质：** 针对纯向量检索遗漏关键词、小分块语义不完整，设计
  Parent-Child 分块与 Milvus child 召回，按 `parent_id` 返回父上下文并经 BM25+RRF
  融合；50 条检索集 hit@3 由 0.800 提升至 0.860（+6.0pp），MRR@3 提升 0.067。
- **可靠任务运行时：** 针对突发告警下任务丢失和多 Worker 放大模型成本，以 Postgres
  保存任务事实，Redis Streams 实现 Pending 回收、重试/DLQ，并用 Redis 租约槽限制
  诊断并发；8/8 个 Worker 任务成功，峰值严格受限于 2/2。
- **分层评测门禁：** 针对“产生报告不等于流程正确”，新增 32 条 Query、9 条 Lifecycle 和
  16 条 Diagnosis Fixture 纯离线回归，覆盖多意图、越权、Prompt Injection、数据源失败、人工拒绝
  与 Deep 降级；固定数据集 SHA-256 和发布阈值，当前三套小型数据集均 100% 通过。

### 2.2 空间不足时的四点压缩版

- **自适应诊断闭环：** 设计 Query/Scope/Capability/WorkflowState 统一契约，将 Fast/Deep 合并为
  Evidence Gate 驱动的自适应诊断，并串联人工根因确认、恢复验证、事故关闭和 Memory 沉淀。
- **证据与权限治理：** 建立 Observed Evidence 的 Scope/ToolCall/fixture 三元绑定和 fail-closed，
  修复真实宿主机污染合成事故；内置 Skill 从 4 个扩展至 7 个，并移除默认容器重启权限。
- **可靠任务运行时：** 针对突发告警与昂贵模型并发，以 Postgres 保存任务事实、
  Redis Streams 实现 Pending 回收/重试/DLQ，并用租约槽将诊断并发限制为 2；
  8/8 个 Worker 任务成功。
- **诊断质量闭环：** 构建 32 Query + 9 Lifecycle + 16 Diagnosis Fixture 的版本化离线门禁，
  覆盖正常/边界、正反例、越权和失败降级；当前小型数据集均 100% 通过，Evidence Isolation 16/16。

### 2.3 STAR 映射（用于面试展开）

- **S（Situation）：** 原项目已有 Fast/Deep、RAG、MCP 和队列，但入口与状态割裂，知识回答、真实
  状态查询、诊断和事故关闭没有闭环；E2E 还发现合成事故被真实宿主机 CPU/内存污染。
- **T（Task）：** 复用既有模块，建立 Query → Scope → Capability → Evidence → RCA → 人工确认 →
  恢复验证 → Memory/Evaluation 的完整生命周期，并让越权、工具失败和证据格式异常可控降级。
- **A（Action）：** 统一 WorkflowState 和 Query/Scope 契约，以 Evidence Gate 合并 Fast/Deep；为
  Observed Evidence 建立 Scope/ToolCall/fixture 绑定；补齐 HITL 生命周期、7 个只读 Skill 以及
  32 Query + 9 Lifecycle + 16 Diagnosis Fixture 的版本化回归。
- **R（Result）：** 形成从咨询到关闭再到持续评测的产品闭环；当前三套小型离线数据集均通过门禁，
  Evidence Isolation 16/16，Skill 从 4 个扩展至 7 个，并移除默认容器重启权限。

### 2.4 不应出现在简历中的说法

- 生产级 AIOps 平台；
- 零幻觉；
- Deep 模式显著提升准确率；
- 本地 Reranker 已提升指标；
- 支撑真实线上百万告警；
- 完整单元/集成/E2E 测试覆盖；
- 已接入 Loki/Elasticsearch/真实 Prometheus 集群。

## 3. 面试开场表达

### 3.1 30 秒版本

> 我做的是一个面向 SRE/OnCall 的 Multi-Agent AIOps 个人项目。系统先通过 Skill Router
> 选择排障 Playbook，Fast Triage 先收集最小证据，Evidence Gate 不满足时保留证据升级 Deep，
> 再经过人工根因确认、同 Scope 恢复验证和事故关闭。系统用 Milvus Hybrid RAG、MCP、Redis Streams
> 和 Postgres 支撑知识、工具与事实链路，并用 57 条纯离线契约/夹具回归约束 Scope、权限、降级和
> Evidence 隔离，而不是只展示几条成功 Demo。

### 3.2 两分钟版本

> 项目的业务问题是：运维诊断不是单轮问答，需要识别故障域、制定计划、调用指标和日志工具、
> 根据结果重规划，并对高风险操作做人审。架构上 Fast 模式是 Skill Router、
> Planner、Executor、Replanner 循环；Deep 模式把指标、日志、基础设施、Runbook 四类取证
> 隔离成专业 Agent，只共享结构化 Evidence。后台路径用 Postgres 记录任务事实，
> Redis Streams 做削峰、重试和 DLQ；三个 Worker 与 Redis 全局执行槽解耦领取并发和真实
> LLM 执行并发。工具侧不是依赖 Prompt，而是经过 Skill、ToolMeta、PermissionMode、
> Guardrail 和审批的逐层决策。
>
> RAG 使用 Parent-Child 切分，Milvus 向量和 BM25 通过 RRF 融合。50 条检索评测中，
> Hybrid hit@3 比纯向量提高 6 个百分点。为了避免只看检索指标，我又补齐了 Router、
> 全量 RAGAS 和最终诊断评测。RAGAS 50 条 faithfulness 是 0.869，而不是早期 5 条样本的
> 1.0。端到端评测则发现 Fast Top-1 50%，Deep 0%；Deep 虽然引用 ID 全有效，但观测对象
> 错了。基于这个问题，我把 Query、Scope、Capability、Evidence 和生命周期统一到 WorkflowState，
> 将 Fast/Deep 改为 Evidence Gate 驱动的自适应诊断，并为 Observed Evidence 增加 Scope、ToolCall ID
> 和 fixture 绑定。当前 32 条 Query、9 条 Lifecycle 和 16 条事故夹具均通过离线门禁，隔离检查
> 16/16；但这只证明确定性契约，真实远程 Evidence Provider 和生产事故 Gold 仍是下一步。

## 4. 亮点二：Fast/Deep 双 Agent 图与 Evidence 架构

### 4.1 问题—方案—结果链

**问题：** 单轮 RAG 问答不能根据工具结果调整计划；单个自由 ReAct Agent 在复杂事故中又容易遗漏
数据源、无限循环或累积过多上下文。

**方案：** 使用两种受约束的 LangGraph：Fast 通过 Skill Router、Planner、Executor、
Replanner 动态循环；Deep 将 Metric、Log、Infra、Runbook 拆为隔离专业 Agent，
通过 fan-out/fan-in 只归并结构化 Evidence，再进行 RCA 和报告。

**结果：** 形成适配简单交互与复杂取证的两种诊断范式，并通过 E2E 指标发现实际 Fast
平均 43.8 秒/35,991 Token、Deep 14.2 秒/11,983 Token，证明必须以 trace 和 cost
验证图设计，而不能根据模式名推断性能。

### 4.2 面试追问与参考答案（12 题）

#### Q1：为什么需要 Fast 和 Deep 两张图，而不是一张图加参数？

两者的状态模型和控制流不同。Fast 是有序 plan/past_steps 循环，Deep 是固定 fan-out、
Evidence 累加和 reducer/judge 流程。强行放在一张图会产生大量条件边和可选字段，
增加状态不变量和测试复杂度。

#### Q2：Fast 图的终止条件是什么？

Router 若直接生成 OOS response，则跳过后续节点。Replanner 后优先检查 response，
其次处理 pending_reroute，再检查空 plan 防死循环，否则回到 Executor。
此外 Harness 还有步骤、递归、Token 和时间预算。

#### Q3：Planner、Executor、Replanner 为什么要拆开？

Planner 负责目标分解，Executor 负责工具受控执行，Replanner 负责根据可观测结果修正策略。
拆开后每个节点输入输出明确，可以独立配置模型、Prompt、预算和 fallback。

#### Q4：Deep 的四个 Agent 是真正并行的吗？

LangGraph 通过四条 fan-out 边调度节点，并在多入边处 join。`evidences` 使用
`Annotated[List, operator.add]` 做并发归并；未派遣的节点由 dispatch guard 跳过。
实际 LLM/工具并行还受 Agent 内部和全局并发限制。

#### Q5：为什么 EvidencePlan 使用规则而不是 LLM？

派遣只需要从事故文本识别指标、日志、网络、容器、Runbook 等粗粒度故障域。
规则可复现、成本低，也不会因一次 Router LLM 波动多调用几个昂贵 Agent。
缺点是同义表达覆盖有限，复杂场景可升级为规则加分类模型。

#### Q6：为什么专业 Agent 不共享完整对话？

共享会让工具原始输出重复传播，产生上下文膨胀和结论锚定。每个 Agent 独立调查，
只写入 source/type/summary/content/metadata 形式的 Evidence，让下游按证据而不是按“谁说服了谁”
做判断。

#### Q7：`operator.add` 有什么作用？

LangGraph 并行节点可能同时返回 evidences patch。普通 list 字段会相互覆盖，
`Annotated[List, operator.add]` 告诉框架使用列表拼接 reducer，把四路 Evidence 安全汇总。

#### Q8：EvidenceReducer 和 RCAJudge 为什么分开？

Reducer 做确定性去重、评分和候选生成，Judge 做语义根因选择。这样 LLM 不必读取所有原始工具输出，
并且 Judge 失败时可以用 reducer 的最高分候选回退。

#### Q9：专业 Agent 失败会不会让整张图失败？

节点捕获异常并返回带 error metadata 的占位 Evidence，其他 Agent 和 reducer 继续执行。
只有无法形成最终状态的关键异常才应终止。代价是报告必须区分“证据未发现”和“数据源调用失败”。

#### Q10：Fast 为什么在实测中比 Deep 更慢？

Fast 经历多次 Planner/Executor/Replanner 循环，past_steps 和工具结果持续累积；
Deep 的规则可能只派遣少量 Agent，并把结果压缩成 Evidence。因此“节点名称少”不等于调用次数少，
要通过 trace 统计每个节点的 LLM rounds 和 Token。

#### Q11：Deep 0% 是否说明多 Agent 架构无效？

本次失败主要来自数据作用域错误：专业 Agent 读取了运行机器而非目标事故资源。
这说明 Evidence 架构还缺少 resource/environment/time-range 绑定，不能据此得出所有多 Agent
架构无效，但当前实现确实不能声称 Deep 更准确。

#### Q12：如果重新设计 Deep，你会先改什么？

先定义 `EvidenceScope(incident_id, environment, resource_id, time_range)`，
所有工具必须由 scope 解析数据源；无对应数据时明确返回 unavailable。
之后以单 Agent fixture 基线验证，再判断专业 Agent fan-out 是否带来增益。

## 5. 亮点三：Skill-first Agent 与 Router/OOS

### 5.1 问题—方案—结果链

**问题：** 把所有 Playbook 和工具一次性提供给模型，会造成上下文干扰；原 OOS 评测又把普通
回复当成 OOS 命中，指标不可信。

**方案：** Router 阶段只暴露 Skill 元数据，选中后再加载完整 Playbook；使用结构化
`SkillChoice` 和 transition reason。评测中只有 `ROUTER_OUT_OF_SCOPE` 才算 OOS，
异常保留在分母。

**结果：** 40 条总体 75.0%，非 OOS 71.4%，OOS 5/5，LLM 请求失败 0。结果同时暴露
generic_oncall 被过度具体 Skill 抢占的问题。

### 5.2 面试追问与参考答案（12 题）

#### Q1：Skill 和 Tool、Prompt、Workflow 分别是什么？

Skill 是某类故障的排障剧本，包含适用场景、步骤、允许工具和风险；Tool 是原子能力；
Prompt 是模型指令载体；Workflow 是 LangGraph 的控制流。Skill 可以被不同 Workflow 复用，
也可以组合多个 Tool，但它本身不是一个可执行工具。

#### Q2：为什么 Router 不直接使用 embedding 相似度？

Embedding 路由成本低，但对边界语义和“是否属于 OnCall”判断不够稳定，也无法自然输出选择理由。
当前用 LLM structured output 处理语义选择，用确定性规则做异常回退。若 Skill 数量增长，
可以先 embedding 召回少量候选，再让 LLM 最终选择。

#### Q3：什么叫渐进式披露？收益如何证明？

Router 只看 `name + description + triggers`，选中后才把完整 `SKILL.md` 和工具边界交给后续节点。
目前没有严谨测过固定 Token 节省比例，因此不报“节省 60%”；能确定的收益是减少无关 Playbook
进入上下文，并把工具约束与领域剧本绑定。

#### Q4：OOS 判分原来错在哪里？

原逻辑可能把 OOS 输入下的任意非空 response 当成正确，这只能证明模型回复了，不能证明它执行了
OOS 决策。修正后检查结构化 transition reason 是否包含 `ROUTER_OUT_OF_SCOPE`，网络或模型异常
单独记为 ERROR，并仍计入总分母。

#### Q5：为什么 OOS 5/5 不能写成 OOS 准确率 100%？

可以写“本评测集 5/5”，但只有 5 个样本，置信区间很宽，且数据分布不代表线上输入。
如果写“线上 100%”就是过度外推。更严谨的下一步是扩展拒答、闲聊、模糊运维和恶意输入。

#### Q6：Router LLM 失败时怎么处理？

先用确定性规则判断输入是否明显属于 OnCall；属于则回退 `generic_oncall`，明显 OOS 则结束。
同时写 transition reason，让评测和日志能够区分正常选择、兜底和 LLM 失败。

#### Q7：为什么总体只有 75%？

这是历史 40 条 Router 评测的结果。主要错误不是 OOS，而是当时没有专用 Redis/MySQL/K8s 等 Skill，模型倾向选择
`host_resource` 或 `network` 等具体 Skill，而不是 `generic_oncall`。这说明 Skill taxonomy
和 Router prompt 存在“过度具体化”偏差。当前已补充 Redis/MySQL、应用运行时和 Kafka Skill，
但尚未在固定 Provider 配置下重跑该随机性评测，因此不能宣称总体准确率已经提升；K8s 专科仍是缺口。

#### Q8：如果继续优化 Router，你会怎么做？

先扩充困难样本并做混淆矩阵，然后调整 generic Skill 描述和负例；再考虑两阶段路由：
规则/OOS gate → embedding 候选召回 → LLM 结构化选择。最终以相同数据集回归，不能只改 Prompt
后主观试几条。

#### Q9：Skill 的 allowed_tools 是绝对白名单吗？

写入、通知和高风险工具必须被 Skill 显式声明；运行时可以补充 ToolMeta 标记的只读工具，
避免 Skill 漏配导致 Agent 无法取证。之后仍要经过 PermissionMode 和 Guardrail，所以不是单层判断。

#### Q10：如何防止 LLM 返回不存在的 Skill？

结构化模型校验后还会通过 SkillRegistry 验证名称；未知名称回退到 `generic_oncall`。
执行阶段不会因为模型字符串就动态加载任意路径。

#### Q11：为什么需要记录 transition history？

仅看最终 selected_skill 无法区分正常选择、规则兜底、模型失败或 OOS。transition history
为调试、审计和 benchmark 提供机器可判定的原因，也让状态转移不只存在于日志文本。

#### Q12：这部分最大的工程教训是什么？

评测标签必须对应真正想证明的行为。“产生回复”与“做出 OOS 判断”不是一件事。
如果判分代理错了，增加更多样本只会更稳定地得到错误结论。

## 6. 亮点四：Parent-Child Hybrid RAG

### 6.1 问题—方案—结果链

**问题：** 大块利于上下文完整但检索不聚焦，小块利于召回但容易失去 SOP 结构；纯向量又容易漏掉
错误码、命令和指标名。

**方案：** 使用 child 做 embedding 和召回，按 `parent_id` 返回 parent；Milvus dense retrieval
和内存 BM25 sparse retrieval 通过 RRF 融合，并保留可选 Rerank。

**结果：** 50 条检索集上 Hybrid hit@3 0.860，相比纯 Vector 0.800 提升 6 个百分点；
MRR@3 从 0.710 提升到 0.777。

### 6.2 面试追问与参考答案（12 题）

#### Q1：为什么要 Parent-Child，而不是统一 chunk size？

Embedding 需要语义聚焦，小块更容易命中具体错误；LLM 回答需要完整条件、命令和上下文，大块更合适。
因此 child 用于索引，命中后按 `parent_id` 去重并返回 `parent_content`。

#### Q2：Parent 和 Child 的关联如何保存？

每个 child metadata 包含稳定的 `parent_id`、`parent_content`、章节路径、source 和 chunk_index。
检索按相关性排序后对 parent_id 去重，同一父块多个 child 命中只返回一次。

#### Q3：如何避免代码块和表格被切坏？

切分前用正则识别代码块、Markdown 表格、链接和 LaTeX 区域，将其替换成不会被 splitter 切开的
占位符；切分后还原。这样能保住操作命令和表格结构。

#### Q4：为什么 AIOps 场景需要 BM25？

错误码、指标名和命令常是低频精确 Token，例如 `OOMKilled`、`maxclients`。
向量模型可能把它们平滑成一般语义，BM25 则能直接利用词项匹配和逆文档频率。

#### Q5：为什么用 RRF，而不是直接把 BM25 分数和 cosine 相加？

两者量纲不同：BM25 无固定上界，向量相似度范围又依赖实现。RRF 只使用名次，
通过 `Σ weight/(k+rank)` 融合，避免脆弱的分数归一化。

#### Q6：BM25 为什么中文按字切，而不引入 jieba？

BM25 主要负责补充精确英文 Token、错误码和数字，中文语义已经由向量检索覆盖。
按字切降低依赖和启动成本；代价是纯中文长查询的稀疏检索质量有限。

#### Q7：内存 BM25 有什么扩展性问题？

每个进程都要从 Milvus 拉取全量 chunks 建索引，上传后还要刷新；当前查询上限也适合小规模语料。
如果达到十万级以上 chunks，应迁移到 OpenSearch/Elasticsearch，并保留相同的融合接口。

#### Q8：hit@k、MRR@k、recall@k 分别说明什么？

hit@k 看是否命中至少一个 gold；MRR 关注首个正确结果的位置；group recall 看多个必要知识点组覆盖多少。
三者一起才能区分“偶尔命中”“排名好”和“证据覆盖完整”。

#### Q9：为什么 gold 要支持组内 OR、组间覆盖？

多个文档可能表达同一个知识点，强制全部命中会误罚。`[[A,B],[C,D]]` 表示 A/B 是第一组替代来源，
C/D 是第二组替代来源；组内命中任意一个即可，组间计算覆盖率。

#### Q10：Rerank 为什么没有写进量化收益？

本机运行时 `bge-reranker-v2-m3` 与 tokenizer 依赖不兼容，发生静默降级。
为了保证实验配置明确，全量结果使用 `--no-rerank`。没有有效 A/B 数据就不能声称它有提升。

#### Q11：Hybrid 提升 6 个百分点是否显著？

50 条数据只能说明当前语料和题集上的方向性增益，不能直接泛化。应进一步用 paired bootstrap
或扩充样本估计置信区间，并查看增益集中在哪些精确 Token 场景。

#### Q12：如果检索命中了正确文档，为什么最终诊断仍可能错？

检索只提供候选证据，Agent 还可能选错 Skill、调用错误环境的工具、忽略证据或错误归因。
因此检索指标必须与生成、引用和根因 Top-1 分层评测。

## 7. 亮点五（生成质量）：全量 RAGAS 与可恢复评测工程

### 7.1 问题—方案—结果链

**问题：** 早期只跑 5 条 Redis smoke test，却把 faithfulness=1.0 外推成总体“零幻觉”；
长评测还可能因单条 API 失败而全部重跑。

**方案：** 为 50 条 RAGAS 增加逐条异常隔离、原子 checkpoint、resume、配置校验和完整运行元数据，
并明确关闭失效 reranker。

**结果：** 50/50 成功，耗时约 33.9 分钟；faithfulness 0.869、answer relevancy 0.885、
context precision 0.883、context recall 0.882、groundedness 0.958、helpfulness 0.898。

### 7.2 面试追问与参考答案（12 题）

#### Q1：RAGAS 的四项指标分别测什么？

Faithfulness 看回答声明是否由 context 支持；answer relevancy 看回答是否针对问题；
context precision 看相关上下文排序和纯度；context recall 看 context 是否覆盖 reference answer
需要的信息。

#### Q2：为什么还要 OpenEvals？

RAGAS 指标偏标准化分解，OpenEvals 的 groundedness/helpfulness 可以从另一套 rubric
检查证据支撑和实际帮助性。双评委不能消除 judge bias，但能减少只依赖单一评分框架。

#### Q3：为什么 5 条 smoke test 不能代表总体？

5 条都来自 Redis，分布单一且样本方差大。Smoke test 只能验证链路能跑，
不能估计十个场景的总体质量，更不能支持“零幻觉”这种强结论。

#### Q4：checkpoint 为什么要原子写？

进程可能在 JSON 写到一半时中断。先写 `.tmp`，再用 replace 切换，可以避免 resume
读到半个 JSON 文件。

#### Q5：resume 如何防止复用错数据？

checkpoint 保存 dataset IDs、模型、检索参数、rerank 配置等 run config；端到端评测进一步保存
数据集 SHA-256。配置或 gold 标签变化时拒绝 resume。

#### Q6：失败条目如何处理？

每个样本单独 try/except，成功结果立即保存，失败记录 error type 和 message。
resume 保留成功项并重试未完成/失败项，避免一条 Provider 抖动让前面几十条作废。

#### Q7：为什么要保存 rows_requested、succeeded、failed？

只输出均值可能掩盖大量失败。例如只成功 10/50 条的高分不能与 50/50 完成的结果比较。
完成率是评测可信度的一部分。

#### Q8：faithfulness 0.869 应该怎么解释？

它说明 judge 认为多数声明有上下文支持，但不是“86.9% 事实绝对正确”，也不是线上幻觉率。
分数受题集、retrieved context、judge 模型和 rubric 影响。

#### Q9：groundedness 0.958 为什么高于 faithfulness？

两个框架的提示词、拆分方式和评分尺度不同，不能直接当成同一标尺。
差异本身提示需要查看逐条失败样本，而不是只比较均值。

#### Q10：LLM-as-a-Judge 有哪些风险？

包括自偏好、位置偏差、输出格式失败、对长上下文遗漏以及同 Provider 相关性偏差。
缓解手段包括固定 temperature、保存理由、抽样人工复核、多 judge 和稳定数据集。

#### Q11：为什么没有计算置信区间？

当前 Runner 输出均值和逐条明细，尚未加入 bootstrap 置信区间。面试中应承认这是统计层面的不足；
如果继续做，会对每项指标和配置差异做 paired bootstrap。

#### Q12：这部分最能体现什么工程能力？

不是“会调用 RAGAS”，而是知道长时外部评测需要可恢复、可审计和配置可比，
并能主动推翻对自己有利但样本不足的旧结论。

## 8. 亮点五（最终诊断）：端到端指标与证据污染发现

### 8.1 问题—方案—结果链

**问题：** 项目只有检索指标，无法回答“诊断到底准不准”；Fast/Deep 名称也没有延迟和 Token 证据。

**方案：** 构建 10 个轻量 AIOps 事故、Fast/Deep 各运行一次；从 Graph 最终 state 计算根因 Top-1、
证据组覆盖、引用有效性和引用正确率，并通过 LangChain callback 采集 Provider Token。

**结果：** Fast Top-1 50%，Deep 0%；Deep citation correctness 94.2%，却系统性错误归因到
本机内存/WSL。Fast 平均 43.8 秒/35,991 Token，Deep 14.2 秒/11,983 Token。
成功解决的是“质量不可见”问题，并定位数据源污染；尚未解决事故级数据隔离。

### 8.2 面试追问与参考答案（12 题）

#### Q1：Root Cause Top-1 如何定义？

每个样本把根因拆成多个因果机制组，组内同义表达 OR，所有组都命中才算 Top-1 正确。
例如 Redis OOM 需要同时体现 Redis、OOM/maxmemory 和 TTL/eviction 机制。

#### Q2：为什么不用 LLM Judge 直接判根因正确？

当前小数据集可以使用确定性关键词组，成本低、可复现，也方便定位判分错误。
缺点是同义表达覆盖有限；规模扩大后可采用“确定性规则 + 双 LLM judge + 人工抽查”。

#### Q3：引用有效性和引用正确率有什么区别？

有效性只检查 `ev_3` 是否真实存在；正确率还要求被引用 Evidence 覆盖 gold evidence groups。
一个格式合法的引用可能引用了无关指标，所以两者必须拆开。

#### Q4：为什么 Deep 引用正确率很高，根因却是 0？

Evidence 内容确实来自工具，引用 ID 也能解析，但工具读取的是运行 Agent 的本机，
而合成事故描述的是另一目标环境。它正确引用了错误作用域的数据。

#### Q5：这算数据污染还是模型幻觉？

主要是 observability scope contamination。模型没有凭空捏造 88% 内存，
但错误地把本机观测当成目标服务证据并建立因果关系，属于 grounding scope 错误。

#### Q6：如何从架构上解决？

让工具调用必须携带 `incident_id/environment/resource_id/time_range`，由运行时解析成绑定的数据源；
Evidence 同样保存这些字段。无法解析作用域时应返回“无目标证据”，而不是自动读取本机。

#### Q7：为什么本项目没有继续实现这个方案？

当前目标是停止新增模块，沉淀技术和面试材料。因此文档明确把它记录为已定位问题和设计方案，
不冒充已落地能力。

#### Q8：Fast 的 citation 为什么是 0？

Fast state 只有 response 和 past_steps，没有稳定 Evidence 实体 ID。即使报告文本出现 `ev_0`，
也无法验证它映射到哪个对象，所以评测保守记 0。这暴露了 Fast 的审计能力缺口。

#### Q9：Token 是怎么统计的？

实现 `BaseCallbackHandler.on_llm_end`，优先读取 message `usage_metadata`，
兼容 `response_metadata.token_usage` 和 `llm_output.token_usage`，按模式累计 input/output/total。

#### Q10：为什么 Fast 比 Deep 更慢、更贵？

当前 Fast 会经历 Planner、多个 Executor/Replanner 回合，历史工具结果不断进入上下文；
Deep 的 EvidencePlan 可能只派少量专业 Agent，并用压缩 Evidence 结束。名称不代表实际图复杂度，
所以必须以 trace 和 usage 测量。

#### Q11：评测器自身出现过什么 bug？

Fast 根因章节标题带“二、”，旧正则没有抽取成功，回退到报告前 1200 字，
把问题描述中的 gold 关键词也算入根因，使 Top-1 虚高到 70%。加入中文序号匹配和单元测试后，
复用保存输出重判为 50%。

#### Q12：这个失败结果为什么仍适合写简历？

因为价值不在包装高分，而在建立业务终局指标、发现“引用正确但诊断错误”的系统性缺陷，
并能给出作用域隔离方案。这比只展示几个成功 Demo 更能体现工程判断力。

## 9. 亮点一：端到端任务架构、事实审计与 Agent 安全

### 9.1 问题—方案—结果链

**问题：** LLM 诊断耗时长，告警洪峰会阻塞 API；模型还可能调用高风险工具，失败重试可能重复副作用。

**方案：** Postgres 先记录事实，Redis Streams 负责优先级队列、Consumer Group、Pending 回收和
DLQ；Redis 分布式槽限制真实诊断并发。工具侧通过 Skill、ToolMeta、PermissionMode、
Guardrail 和 Postgres 审批逐层收窄。

**结果：** 历史环境中 200 个后台提交和 500 个 Webhook 均 100% 接收；3 个 Worker 存活时真实诊断
始终不超过 2 个执行槽。系统保留 Task、AgentRun、ToolCall、Evidence、Approval 和 Report 事实。

### 9.2 面试追问与参考答案（12 题）

#### Q1：为什么选择 Redis Streams，而不是 Celery/RabbitMQ？

项目已经依赖 Redis，Streams 提供持久消息、Consumer Group、PEL 和 ACK，足够展示后台诊断语义。
Celery 生态更成熟、RabbitMQ 路由能力更强；当前选择是参考项目规模和依赖复杂度的权衡。

#### Q2：Redis Streams 能保证消息不重复吗？

不能。Consumer 崩溃或 ACK 前超时会导致消息被重新认领，本质是至少一次投递。
业务层需要任务状态机、幂等键和副作用保护。

#### Q3：PEL 和 XAUTOCLAIM 分别做什么？

PEL 保存已投递给 Consumer 但尚未 ACK 的消息。`XAUTOCLAIM` 允许其他 Worker
认领 idle 时间超过阈值的 Pending 消息，恢复崩溃 Worker 遗留任务。

#### Q4：为什么要 DLQ？

可重试故障和永久故障不能无限混在主队列。超过最大 attempts 后把消息和错误原因写入 DLQ，
便于人工分析、告警和有控制地重放。

#### Q5：为什么 Redis 不是事实权威？

Redis 保存的是运行协调状态，可能过期或被清理；Postgres 保存 Alert、Task、ToolCall、
Evidence 等长期事实。当前没有独立 `reports` 表，最终报告保存为 `diagnosis_report`
Evidence 并通过 output reference 关联。即使队列消息消失，也应能从数据库解释任务发生过什么。

#### Q6：Worker 数量为什么不等于真实并发？

Worker 可以先领取消息并进入等待状态，但昂贵诊断必须获取 Redis 全局执行槽。
历史压测中 3 个 Worker 都参与过，`running` 可达到 3，但实际槽一直是 2/2。

#### Q7：分布式执行槽如何避免永久泄漏？

槽持有需要 TTL/续期，任务结束释放；Worker 崩溃后租约最终过期。
严格实现还应使用唯一 owner token 和原子 Lua 校验，避免错误释放别人的槽。

#### Q8：固定窗口限流有什么缺点？

窗口边界可能出现突发双倍流量，且单 IP 不等于真实租户。生产中可改滑动窗口或令牌桶，
并按租户、来源和接口分别限流。

#### Q9：PermissionMode 有哪些状态？

`read_only` 只允许只读工具；`normal` 遵守默认风险策略；`ask_destructive`
允许高风险调用进入人工审批；`bypass` 仅供开发，不应暴露到生产。

#### Q10：为什么等待审批时要释放执行槽？

人工响应可能需要分钟级。如果一直占用稀缺诊断槽，会造成队头阻塞。
工具运行器在等待前 pause slot，决策后 resume，再继续执行或返回拒绝结果。

#### Q11：Tool 白名单能否完全防止危险调用？

不能单独保证。模型可能猜工具名，ToolMeta 也可能标错风险，所以还需要运行时二次校验、
输入感知权限、审批、工具自身幂等和基础设施账户权限。

#### Q12：历史 197 req/s 能不能写成系统吞吐？

只能写“特定本机历史压测中 Webhook 接入 197 req/s”，不能等同真实诊断吞吐。
该测试主要验证接入、落库和入队，Worker 曾被停止；真实 LLM 执行并发由 2 个槽限制。

## 10. 综合追问

### Q1：你认为项目最有创新性的地方是什么？

不是使用了某个框架，而是将检索、工具 Evidence 和最终根因拆成可分别评测的层次，
并用端到端结果证明“grounded citation 仍可能导致错误 RCA”。这是 AIOps Agent 比普通 RAG
更需要关注的作用域一致性问题。

### Q2：你做过最关键的错误修正是什么？

一是 OOS 不能用“非空 response”判分；二是 Fast 根因抽取不能把问题描述当答案。
两次修正都让指标下降，但提高了可信度。

### Q3：如果面试官说 Deep 0% 说明项目失败，你怎么回答？

Deep 当前诊断结果确实不合格，不能回避。但这次评测成功暴露了具体的系统性原因：
数据源没有按事故实体隔离，而不是简单“模型能力差”。项目价值在于建立了能够发现该问题的指标，
并形成可执行的隔离设计。

### Q4：如果只能保留一个量化指标，你选哪个？

选择根因 Top-1，因为它最接近用户最终价值；但上线决策仍要同时看 citation、failure rate、
latency 和 cost，单指标容易被优化作弊。

### Q5：你最希望面试官看到什么能力？

能够从“功能跑通”继续追问“指标是否证明业务价值”，主动修正对自己有利的假高分，
并把问题定位到架构边界，而不是只调 Prompt。

## 11. 面试现场回答方法

### 11.1 每条经历都用六段式

```text
1. 业务问题
2. 原方案为什么不够
3. 我的设计
4. 关键源码/数据结构
5. 实验和量化结果
6. 局限与下一步
```

### 11.2 遇到不会的问题

推荐回答：

> 这个点当前项目没有实现，我不会把设计当作完成情况。从现有边界看，我会先……
> 验证方式是……，主要风险是……。

不要使用：

> 应该没问题、框架会自动处理、生产上一般就是这样。

### 11.3 被问项目来源与个人完成度

推荐回答：

> 这是我的个人学习项目，我会按端到端系统介绍自己真正掌握并能够复现的部分。
> 架构、双图、RAG、队列和权限模块我都能沿源码解释；具体实现来源和个人修改范围会如实说明，
> 不把参考代码、历史报告或未完成设计说成个人从零原创。

这个回答不会减分，反而能够建立可信度。真正减分的是无法区分原有设计和个人贡献。

## 12. 最终背诵卡片

```text
项目价值：
OnCall Agent + RAG + Tool + Queue + Audit + Evaluation

Fast：
Skill Router -> Planner -> Executor -> Replanner -> Report

Deep：
Context -> Evidence Plan -> 4 specialists -> Reducer -> RCA -> Report

RAG：
Parent-Child -> Milvus Vector + BM25 -> RRF -> optional Rerank

真实指标：
Router 40：75.0%，OOS 5/5
Retrieval 50：Hybrid hit@3 0.860 vs Vector 0.800
RAGAS 50：faithfulness 0.869，groundedness 0.958
E2E 10×2：Fast Top-1 50%，Deep 0%
Deep citation correctness 94.2%
Fast/Deep 平均 Token：35,991 / 11,983

最重要发现：
正确引用错误作用域的数据，仍会得到错误根因。

掌握标准：
能解释整体设计、关键源码、运行数据、失败模式和改进取舍。
```
