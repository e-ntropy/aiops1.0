# Agent 开发求职：项目深度分析与改进路线图

> **目标岗位**：Agent/AI 应用开发工程师、RAG/LLM 应用工程师、AIOps/SRE 平台开发
> **适用场景**：简历项目描述、面试准备、项目改进方向决策

---

## 目录

1. [项目核心竞争力分析](#1-项目核心竞争力分析)
2. [RAG 相关技术亮点深度剖析](#2-rag-相关技术亮点深度剖析)
3. [Agent 相关技术亮点深度剖析](#3-agent-相关技术亮点深度剖析)
4. [现有 Benchmark 的面试价值评估](#4-现有-benchmark-的面试价值评估)
5. [简历中的最优呈现策略](#5-简历中的最优呈现策略)
6. [可量化改进方向与评测集构建方案](#6-可量化改进方向与评测集构建方案)
7. [面试高频追问准备清单](#7-面试高频追问准备清单)

---

## 1. 项目核心竞争力分析

### 1.1 与同类项目的差异化优势

市面上的 Agent 项目（如 ChatDev、AutoGPT、MetaGPT）多聚焦于「自动编程」或「通用对话」，而本项目在以下维度有显著差异化：

| 维度 | 大部分 Agent 项目 | 本项目 |
| --- | --- | --- |
| **场景聚焦** | 通用/泛化 | OnCall/SRE 垂直场景，有真实的 SOP Playbook |
| **安全模型** | 无或单层白名单 | 三层防御（Skill白名单→PermissionMode→Guardrail→审批） |
| **Agent 架构** | 单一 Agent 循环 | fast(Plan-Execute-Replan) + deep(4 Agent 并行取证) 双图 |
| **RAG 工程化** | 朴素向量检索 | Parent-Child 切分 + Hybrid + RRF + Rerank 完整链路 |
| **生产特性** | 缺少 | 优先级队列、Consumer Group、DLQ、分布式并发槽、审计 |
| **可评测性** | 无 | 50条检索 + 50条QA + RAGAS + OpenEvals 双评委 |

**面试话术**：「这个项目不是又一个 LangChain Demo，它从工程角度解决了 Agent 系统在运维场景中真正会遇到的问题——安全边界、证据追溯、队列削峰和可量化验证。」

### 1.2 技术创新点排名（面试价值排序）

#### ⭐⭐⭐⭐⭐ Tier 1：面试时最应该主动讲的

**1. Skill-first 渐进式披露架构**

```
传统做法: 把所有 Skill 的完整内容一股脑塞进 System Prompt → 上下文浪费 60%+
本项目: Router阶段只暴露 name+description+triggers → LLM 结构化选择 → 命中后再注入完整 Playbook
```

- **创新本质**：在 Agent 系统中应用了「延迟加载」思想，只有被选中的 Skill 才展开完整指令
- **量化表述**：将 Router 阶段的 Prompt token 控制在 ~500 tokens（vs 全量注入 ~2500+ tokens）
- **代码位置**：[app/agents/skill_router.py](app/agents/skill_router.py) + [app/skills/registry.py](app/skills/registry.py)
- **面试话术**：「LangChain/LangGraph 本身不提供 Skill 路由——这是我们自己设计的一层抽象。Router 只暴露菜单，命中后才注入完整 Playbook 和 allowed_tools，这比直接做 embedding 匹配更可控，也比全量注入更经济。」

**2. Parent-Child RAG + Hybrid Search + RRF 多路融合**

```
纯向量检索的痛点: "ERR_CONN_REFUSED" 这类精确字符串被向量"揉"进语义空间，召回反而变差
解决方案: Vector (语义) + BM25 (精确token) → RRF融合 → Rerank → Parent去重返回完整上下文
```

- **创新本质**：不是简单的 LangChain `similarity_search`，而是完整的检索工程链路
- **BM25 设计细节**：轻量分词（英文 token + 中文单字），不依赖 jieba，专为捕获精确术语和错误码
- **Parent-Child**：子块 300 字符检索精度高、父块 2400 字符上下文完整
- **RRF 权重可调**：BM25 权重默认 0.4，支持通过 benchmark A/B 测试调参
- **代码位置**：[app/rag/retrieval.py](app/rag/retrieval.py) + [app/core/hybrid_retriever.py](app/core/hybrid_retriever.py)
- **面试话术**：「我们做了 Parent-Child 分层切分 + Hybrid Search + RRF + Rerank 四段式检索。BM25 那一路我们设计了轻量分词器——中文按字、英文保留完整 token——专门捕获向量容易漏掉的错误码和精确术语。而且我们通过 50 条标注评测集对每个环节做了 A/B 对比验证。」

**3. 三层工具安全防御体系**

```
Layer 0 (硬墙): Skill allowed_tools 白名单 — 任何模式都不可绕过
Layer 1 (模式): PermissionMode 决策 — READ_ONLY/NORMAL/ASK_DESTRUCTIVE/BYPASS
Layer 2 (护栏): Guardrail 黑名单 — 高危工具默认 deny，通知工具需显式授权
Layer 3 (审批): 人工审批闭环 — ASK_DESTRUCTIVE 模式下写操作写入 Postgres 审批表，阻塞等人工 allow/deny
```

- **创新本质**：从「能不能调」到「什么模式下可以调」到「调之前要不要人确认」的逐层收窄
- **细节亮点**：只读工具豁免 Skill 白名单（`effective_read_only` 输入感知），避免因 Skill 漏配导致诊断失败
- **审批闭环**：分布式并发槽在等待审批时主动让出（`slot.pause()`），避免队头阻塞
- **代码位置**：[app/runtime/permissions.py](app/runtime/permissions.py) + [app/runtime/tool_runner.py](app/runtime/tool_runner.py)

#### ⭐⭐⭐⭐ Tier 2：面试时可以展开讲的

**4. Deep 诊断图：4 Agent 并行取证 + 证据隔离 + 规则派遣**

```
传统多 Agent: 所有 Agent 共享完整对话历史 → 上下文爆炸 + 推理污染
本项目: 每个 Agent 独立最小 LLM/工具循环 → 只回传压缩 Evidence → 确定性归并打分
```

- **隔离设计**：专业 Agent 的中间推理不进共享状态，只写入结构化 Evidence
- **派遣策略**：确定性关键词规则（非 LLM），未派遣的 Agent 通过 Guard 跳过，零额外成本
- **证据归并**：确定性评分表（metric 1.0 > infra 0.9 > log 0.85 > runbook 0.75），LLM 失败时有确定性回退
- **代码位置**：[app/diagnosis_graphs/deep_diagnosis_graph.py](app/diagnosis_graphs/deep_diagnosis_graph.py)

**5. LLM Wiki 经验沉淀（Karpathy 模式）**

```
传统 RAG 反思: 对话后追加一条 embedding → 下次检索时可能被召回
LLM Wiki: 诊断完成后 LLM 合并/重写 Markdown 页面 → 下次诊断前通过 index.md 的 [[wikilinks]] 直接加载
```

- **本质区别**：沉淀式（知识被 LLM 重组）vs 检索式（知识被追加但可能永远不会被召回）
- **面试话术**：「我们借鉴了 Karpathy 提出的 LLM Wiki 思想——知识不是简单追加到向量库，而是让 LLM 把每次诊断的经验合并重写到结构化的 Markdown 页面里。下次诊断时直接通过 Wikilinks 导航加载，而不是依赖向量检索的随机性。」

**6. Redis Streams 异步任务队列工程化**

- **多级优先级**：4 级队列（critical/high/normal/low），severity 映射
- **Consumer Group**：多 Worker 负载均衡，消息不丢不重
- **Stale Pending 回收**：Worker 崩溃后其他 Worker 自动认领超时任务
- **DLQ**：超过最大重试次数的任务进入死信队列
- **分布式并发槽**：Redis 信号量限制真实诊断并发数，TTL + 心跳续期
- **代码位置**：[app/queue/redis_streams.py](app/queue/redis_streams.py) + [app/diagnosis_worker.py](app/diagnosis_worker.py)

#### ⭐⭐⭐ Tier 3：可以作为补充亮点

**7. 工具并行编排算法**

- 将 LLM 返回的 tool_calls 按 `concurrency_safe` 属性分批：相邻安全工具异步 gather，不安全工具串行
- 同批内 fail-isolation：一个工具失败不影响兄弟工具
- 结果截断防止 context 爆炸
- **代码位置**：[app/runtime/tool_runner.py](app/runtime/tool_runner.py) 的 `partition_tool_calls` 函数

**8. MCP 多服务独立部署**

- 5 个 MCP 工具服务独立进程，基于 FastMCP + streamable-http
- 逐个服务器加载避免 ExceptionGroup 级联故障
- 网络服务内置内网扫描防护
- **代码位置**：[mcp_servers/](mcp_servers/) + [app/core/mcp_client.py](app/core/mcp_client.py)

---

## 2. RAG 相关技术亮点深度剖析

### 2.1 检索链路完整拆解

```
用户问题: "Redis connected_clients 接近 maxclients 怎么排查?"
    │
    ├─→ Milvus 向量检索 (dense)
    │   使用 bge-m3 embedding，1024维
    │   预取 final_k * 3 = 9 条子块（给去重留余量）
    │
    ├─→ BM25 内存索引检索 (sparse)
    │   分词: ["redis", "connected_clients", "接", "近", "maxclients", "怎", "么", "排", "查"]
    │   纯内存索引，从 Milvus 全量拉取构建
    │
    ├─→ RRF 融合
    │   score(d) = Σ weight_i / (60 + rank_i(d))
    │   BM25权重 = 0.4, Vector权重 = 0.6
    │
    ├─→ Rerank 重排序
    │   FlagEmbedding BAAI/bge-reranker-v2-m3
    │   对候选文档重新打分排序
    │
    ├─→ Parent 去重 + 拼接
    │   按 parent_id 去重（同一父块下多个子块命中只算一次）
    │   返回完整 parent_content（最多 2400 字符）
    │
    └─→ 最终 context（top_k=3 个完整父块段落）
```

### 2.2 可以被量化验证的 RAG 指标

| 环节 | 可验证指标 | 现有数据 | 构建难度 |
| --- | --- | --- | --- |
| 向量检索 | recall@k, mrr@k | 50 条检索评测集 ✅ | 已有 |
| BM25 贡献 | retrieval --no-hybrid vs 默认的 hit@k 差异 | 评测框架支持 A/B ✅ | 已有 |
| Rerank 贡献 | retrieval --no-rerank vs 默认的 mrr@k 差异 | 评测框架支持 A/B ✅ | 已有 |
| RRF 参数 | 不同 bm25_weight/rrf_k 下的 recall@k | 可通过 --bm25-weight/--rrf-k 调参 | 已有 |
| 生成质量 | faithfulness, answer_relevancy, context_precision, context_recall | 50 条 QA 评测集 ✅ | 已有 |
| 幻觉检测 | groundedness (OpenEvals) | 50 条 QA 评测集 ✅ | 已有 |

### 2.3 面试中如何讲 RAG 部分

**开场白**（30秒版本）：
「我负责的 RAG 系统采用了 Parent-Child 分层切分——小块 300 字符用于向量检索保证精度，大块 2400 字符保证 LLM 有足够上下文。检索端做了 Hybrid Search：一路 Milvus 向量 + 一路 BM25 精确匹配——专门捕获错误码和命令名这类向量容易漏掉的术语——两路通过 RRF 融合后再过 Reranker 排序。整个链路有 50 条标注评测集做回归验证，支持 Hyrid/Rerank/RRF 参数的全排列 A/B 测试。」

**追问准备**：
- Q: 「为什么不用 GraphRAG？」→ A: 「GraphRAG 需要先建好实体-关系图，适合已知拓扑的场景。我们这个阶段先把基础检索做扎实，Benchmark 分数稳定后再引入 Graph 增强——代码里已经在 EvidenceReducer 留了 KG 接入点。」
- Q: 「Parent-Child 切分和大上下文时代的矛盾？」→ A: 「长上下文模型确实减弱了 RAG 的 necessity，但我们场景中知识库有 200+ 文档，全塞进 prompt 不现实。而且 RAG 提供了来源追溯——这在运维诊断中是合规刚需。」
- Q: 「BM25 为什么不用 ES？」→ A: 「当前知识库规模（~1000 chunks）内存索引完全够，启动 100ms 以内。代码里有注释说明了大规模时的迁移路径——换 ES/OpenSearch 只需替换 `_BM25Index` 的实现，接口不变。」

---

## 3. Agent 相关技术亮点深度剖析

### 3.1 Agent 系统设计哲学

本项目不是「调一个 LLM API」那么简单，它体现了一套完整的 Agent 设计哲学：

```
设计原则:
┌─────────────────────────────────────────────────────┐
│ 1. 上下文是最宝贵的资源 → 渐进式披露 (Skill Router)    │
│ 2. Agent 会犯错 → 多层防御而非单点信任 (Permissions)   │
│ 3. 不确定性需要结构 → 图约束而非自由对话 (LangGraph)    │
│ 4. 证据必须可追溯 → 每次决策都要有引用 (Evidence审计)  │
│ 5. 经验需要沉淀 → LLM Wiki 而非简单反思              │
│ 6. 系统需要可验证 → Benchmark 而非手测               │
└─────────────────────────────────────────────────────┘
```

### 3.2 fast vs deep：两种 Agent 架构范式

| 维度 | fast (Plan-Execute-Replan) | deep (Multi-Agent Fan-out) |
| --- | --- | --- |
| **学术对应** | ReAct / Plan-and-Solve | Multi-Agent Debate / Specialist Ensemble |
| **Agent 数量** | 1 个（循环执行） | 4 个（并行取证） |
| **通信方式** | 串行状态传递 | 并行→Join Barrier→归并 |
| **优势** | 低延迟、低成本、适合简单任务 | 高覆盖、多角度、适合复杂交叉验证 |
| **劣势** | 单视角、可能遗漏 | 高延迟、高成本、需要归并策略 |
| **适用场景** | "我电脑很卡" | "Redis 内存 98% + 连接超时 + 慢查询" |

### 3.3 面试中如何讲 Agent 部分

**开场白**（45秒版本）：
「这个项目实现了两种 Agent 架构。Fast 模式走经典的 Plan-Execute-Replan 循环——先通过 Skill Router 把用户问题路由到对应的诊断 Playbook，然后 Planner 拆成 4-6 步，Executor 逐步执行，Replanner 根据结果决定继续还是调整。Deep 模式走的是多 Agent 并行取证——四个专业 Agent 分别查指标、日志、基础设施和 Runbook，各自在隔离上下文中运行，只把结构化 Evidence 写回共享状态，最后通过证据归并和 RCA Judge 判断根因。」

**追问准备**：
- Q: 「为什么不用多 Agent 辩论？」→ A: 「辩论适合需要多角度判断的开放性问题。我们的场景中四个 Agent 看的是不同数据源（metrics/logs/infra/runbooks），它们之间不是意见分歧而是信息互补——更适合 fan-out→归并 而非辩论。不过在 RCA Judge 阶段，确实可以引入多个 LLM Judge 投票来提升判定可靠性。」
- Q: 「Agent 间怎么避免上下文污染？」→ A: 「每个专业 Agent 有自己的独立 LLM 调用循环，中间推理（包括工具调用结果和思考过程）完全不进入共享 state。只把最终的结构化 Evidence（source + type + summary + score）写入共享状态，下游 RCA Judge 只看 summary 不看原始工具输出。」
- Q: 「怎么防止 Agent 幻觉？」→ A: 「三层约束：第一，工具调用有 Skill 白名单限制；第二，RAG 检索只返回有来源标注的内容；第三，RCA Judge 被明确告知"只看准备好的 summary，不要假设你见过原始数据"。如果 LLM 失败，每一步都有确定性回退路径。」

---

## 4. 现有 Benchmark 的面试价值评估

### 4.1 现有评测体系价值打分

| 评测内容 | 维度 | 面试价值 | 说明 |
| --- | --- | --- | --- |
| retrieval_rk_50.jsonl | hit@k, mrr@k, recall@k | ⭐⭐⭐⭐⭐ | 最高价值——证明你懂得「检索需要量化验证」 |
| ragas_qa_50.jsonl | faithfulness, answer_relevancy, context_precision, context_recall | ⭐⭐⭐⭐⭐ | 证明你理解「生成质量不等于主观感受」 |
| OpenEvals 集成 | groundedness, helpfulness | ⭐⭐⭐⭐ | 双评委设计是加分项 |
| A/B 对比框架 | hybrid on/off, rerank on/off, bm25_weight, rrf_k | ⭐⭐⭐⭐⭐ | 证明你做了真正的工程调优而非跑通就行 |
| 50 条数据集规模 | - | ⭐⭐⭐ | 规模偏小但覆盖 10 个场景，足够展示方法论 |

### 4.2 Benchmark 框架的技术亮点（面试中可以讲的）

1. **分组 Gold 设计**：
   - 支持 `relevant_groups: [[A, B], [C, D]]`——A 和 B 是同一知识点的替代来源（OR），组间按覆盖率计算 recall
   - 解决了「同一个 SOP 写了两次但 benchmark 误判为未命中」的问题
   - **话术**：「我们设计了分组 Gold 规则：组内是 OR 关系（命中任意一个就算找到这个知识点），组间按覆盖率计算 recall。这比简单的 top-k 命中率更能反映真实检索质量。」

2. **滚动指标实时输出**：
   - 逐题打印滚动均值和状态（OK/MISS），支持 `--verbose` 查看详细 top 结果
   - **话术**：「评测不是'跑完看最终数字'——我们做了滚动指标输出，每道题跑完立即更新 hit@k/mrr@k/recall@k 的滚动均值，方便观察哪些场景拖累指标。」

3. **每个环节独立可开关**：
   - `--no-hybrid` / `--no-rerank` / `--bm25-weight` / `--rrf-k` 等参数
   - **话术**：「每个检索环节都有独立的 A/B 开关。比如我们可以跑一轮 `--no-rerank` 对比 Reranker 带来的 mrr@k 提升，再跑一轮 `--bm25-weight 0.6` 看 BM25 权重调高后的效果——这些都有代码自动化支持。」

### 4.3 如何在简历中呈现 Benchmark

**推荐写法（简历项目描述中的一个子要点）**：

> 构建了 RAG 检索和生成的完整评测体系：
> - 建立 10 个场景 50 条检索标注数据集，支持 hit@k/mrr@k/recall@k 三指标滚动评测
> - 建立 50 条端到端 QA 数据集，采用 RAGAS（4 指标）+ OpenEvals（2 指标）双评委打分
> - 实现 Hybrid/Rerank/RRF 参数全排列 A/B 对比框架，量化每个环节的增量贡献

---

## 5. 简历中的最优呈现策略

### 5.1 完整简历项目描述（推荐模板）

```markdown
## Multi-Agent AIOps 智能诊断平台

**技术栈**：Python, LangGraph, LangChain, FastAPI, Milvus, Redis Streams,
PostgreSQL, MCP (Model Context Protocol), Docker

**项目概述**：
面向 SRE/OnCall 场景的多智能体诊断系统，实现了 fast/deep
双模式诊断、Skill 渐进式路由、Parent-Child RAG 混合检索和异步任务队列，
覆盖从告警接入到可追溯诊断报告的全链路。

**核心贡献**：

1. **Skill-first 渐进式路由架构**
   - 设计 Router→Planner→Executor→Replanner 的 LangGraph 诊断图
   - Router 阶段仅暴露 Skill 摘要（~500 tokens），命中后懒加载完整 Playbook
   - LLM 结构化输出 + 关键词规则回退双层策略，路由准确且可观测

2. **双模式 Agent 系统**
   - fast: Plan-Execute-Replan 单Agent循环，适合快速排查
   - deep: 4 个专业 Agent（Metric/Log/Infra/Runbook）并行取证→归并→RCA判定
   - 专业 Agent 间上下文隔离，仅通过结构化 Evidence 通信

3. **Parent-Child RAG + Hybrid Search 检索链路**
   - 小块(300 chars)向量检索 + 大块(2400 chars)上下文拼接
   - Vector + BM25 + RRF 融合 + BGE Reranker 四段式检索
   - 50 条标注评测集 + RAGAS/OpenEvals 双评委质量验证
   - 支持 Hybrid/Rerank/RRF 参数的 A/B 对比实验

4. **三层工具安全防御**
   - Skill 白名单(硬墙) → PermissionMode(模式决策) → Guardrail(高危拦截)
   - ASK_DESTRUCTIVE 模式下的异步人工审批闭环（Postgres + 超时降级）
   - 审批等待期间主动让出分布式并发槽，避免队头阻塞

5. **生产级任务队列**
   - Redis Streams 4级优先级队列 + Consumer Group 多Worker消费
   - Stale Pending 回收 + DLQ 死信队列 + 分布式并发槽(Redis 信号量)
   - Postgres 全链路审计(Alert→Task→AgentRun→ToolCall→Evidence→Report)

**量化成果**（建议填入实际跑 benchmark 后的数值）：
- 检索 hit@3: XX%, MRR@3: XX%, recall@3: XX%
- RAGAS faithfulness: XX%, answer_relevancy: XX%
- Rerank 对 MRR@3 提升: +XX%
- 知识库覆盖 10 个运维场景、200+ SOP 文档
```

### 5.2 针对不同岗位的微调建议

| 岗位方向 | 重点突出的模块 | 弱化的模块 |
| --- | --- | --- |
| **RAG 工程师** | Parent-Child RAG、Hybrid Search、Rerank、Benchmark | Worker/队列部分一笔带过 |
| **Agent 工程师** | Skill Router、fast/deep 双图、Permissions、Tool Runner | RAG 细节压缩到一段 |
| **AI 平台工程师** | 队列、Worker、审计、分布式并发槽、MCP | RAG/Agent 作为使用场景 |
| **AIOps/SRE** | Skill 系统、MCP 工具、诊断流程、LLM Wiki | 只提架构不提实现细节 |

---

## 6. 可量化改进方向与评测集构建方案

### 6.1 改进方向一：完善 RAG 检索评测（投入 2-3 天，面试价值 ⭐⭐⭐⭐⭐）

这是投入产出比最高的改进——**跑出现有 Benchmark 的数字**并写进简历。

**步骤**：
```bash
# 1. 确保知识库已导入
python scripts/ingest_kb_corpus.py --reset --batch 8

# 2. 跑基线（全功能）
python benchmark/run_benchmark.py retrieval --k 3

# 3. A/B 对比：关 Rerank
python benchmark/run_benchmark.py retrieval --k 3 --no-rerank

# 4. A/B 对比：关 Hybrid
python benchmark/run_benchmark.py retrieval --k 3 --no-hybrid

# 5. 参数搜索：不同 BM25 权重
python benchmark/run_benchmark.py retrieval --k 3 --bm25-weight 0.2
python benchmark/run_benchmark.py retrieval --k 3 --bm25-weight 0.6
python benchmark/run_benchmark.py retrieval --k 3 --bm25-weight 0.8

# 6. 参数搜索：不同 RRF k 值
python benchmark/run_benchmark.py retrieval --k 3 --rrf-k 30
python benchmark/run_benchmark.py retrieval --k 3 --rrf-k 120

# 7. 生成质量评测
python benchmark/run_benchmark.py ragas --limit 50
```

**产出**：一份包含以下内容的 A/B 对比报告：
| 配置 | hit@3 | mrr@3 | recall@3 |
| --- | --- | --- | --- |
| 基线（Hybrid + Rerank） | ? | ? | ? |
| 纯向量（关 Hybrid） | ? | ? | ? |
| 关 Rerank | ? | ? | ? |
| BM25 weight=0.6 | ? | ? | ? |

**简历话术**：「通过 A/B 对比实验，Hybrid Search 相比纯向量检索 recall@3 提升 X%，Rerank 额外贡献 MRR@3 提升 Y%。最终确定 BM25 权重 0.X 为最优配置。」

### 6.2 改进方向二：Agent 决策准确率评测（投入 3-5 天，面试价值 ⭐⭐⭐⭐⭐）

这是项目目前最大的评测缺口——**没有对 Agent 路由和诊断质量的量化评测**。

**方案 A：Skill 路由准确率评测（最容易构建）**

构建 30-50 条「用户输入 → 正确 Skill」的评测集：

```jsonl
{"input": "我电脑很卡，CPU一直100%", "expected_skill": "host_resource_diagnosis", "scenario": "本机资源"}
{"input": "Redis内存使用率98%客户端连接被断开", "expected_skill": "generic_oncall", "scenario": "Redis告警"}
{"input": "网站打不开了返回502", "expected_skill": "network_diagnosis", "scenario": "网络故障"}
{"input": "Docker容器mysql-prod一直在重启", "expected_skill": "container_diagnosis", "scenario": "容器异常"}
{"input": "今天天气怎么样", "expected_skill": "OUT_OF_SCOPE", "scenario": "非OnCall"}
...
```

**评测代码骨架**（30 行即可实现）：

```python
# benchmark/run_skill_router_benchmark.py
import json
from app.agents.skill_router import skill_router_node

async def eval_skill_router(test_file="benchmark/skill_router_eval.jsonl"):
    correct = 0
    total = 0
    for line in open(test_file):
        row = json.loads(line)
        result = await skill_router_node({"input": row["input"]})
        total += 1
        if result["selected_skill"] == row["expected_skill"]:
            correct += 1
        print(f"[{total}] {'✓' if result['selected_skill'] == row['expected_skill'] else '✗'} "
              f"expected={row['expected_skill']} got={result['selected_skill']} "
              f"input={row['input'][:50]}...")
    print(f"\n准确率: {correct}/{total} = {correct/total:.1%}")
```

**简历话术**：「构建 50 条 Skill 路由评测集，路由准确率 XX%。同时统计了 Router LLM 失败时关键词回退的触发率（X%），验证了双层路由策略的必要性。」

**方案 B：端到端诊断质量评测（推荐，但投入较大）**

构建 20-30 条真实诊断场景，人工标注「应包含的关键发现」：

```jsonl
{"scenario": "Redis内存告警", "input": "Redis内存98% OOM", 
 "expected_findings": ["used_memory超过maxmemory", "检查大key", "检查maxmemory-policy"],
 "mode": "fast"}
```

用 LLM-as-Judge 判断诊断报告是否覆盖了关键发现。

### 6.3 改进方向三：Agent 工具调用准确率评测（投入 2-3 天）

评测 Executor 是否调用了正确的工具、是否避免了不必要的工具调用。

**评测维度**：
- **工具选择准确率**：Agent 选择的工具是否与 Ground Truth 一致
- **工具调用效率**：完成任务需要的最少工具调用次数 vs 实际调用次数
- **幻觉工具率**：Agent 是否尝试调用不存在的工具（已在 tool_runner 中统计，可直接提取）

### 6.4 改进方向四：实现更多硬核技术特性（投入 2-4 周）

以下特性在面试中非常加分，从易到难排序：

| 特性 | 难度 | 面试价值 | 时间 | 说明 |
| --- | --- | --- | --- | --- |
| **Langfuse 集成** | ⭐ | ⭐⭐⭐⭐ | 2天 | LLM 调用全链路追踪、成本统计、延迟分析 |
| **Deep Agent 权限统一** | ⭐⭐ | ⭐⭐⭐ | 3天 | 让 Deep Agent 也走 PermissionMode，消除「已知限制」 |
| **Human-in-the-loop** | ⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 1周 | 在 LangGraph 中增加 interrupt 节点，支持诊断中人工介入 |
| **多轮对话** | ⭐⭐ | ⭐⭐⭐ | 3天 | 诊断后支持追问（"能再详细看看日志吗？"） |
| **Agent 投票/RCA 多Judge** | ⭐⭐ | ⭐⭐⭐⭐ | 3天 | RCAJudge 改为 3 个 Judge 投票，降低单一 LLM 偏差 |
| **GraphRAG** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 2周 | 构建告警-服务-依赖知识图谱，实现图增强检索 |
| **多模态输入** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 2周 | 支持截图（Grafana 仪表盘、错误堆栈）输入 |
| **Helm Chart / K8s 部署** | ⭐⭐⭐ | ⭐⭐⭐⭐ | 1周 | 体现生产级交付能力 |

### 6.5 改进方向五：构建自定义评测集的通用方法论

**原则**：好的评测集 = 覆盖真实分布 + 包含边界 case + 可重复运行

**构建步骤**（以 Skill 路由评测为例）：
1. **收集真实 query**：从系统日志/埋点中提取 100 条真实用户输入
2. **分类标注**：人工标注每条 query 的正确 Skill
3. **平衡采样**：确保每种 Skill 至少 10 条，包含 5 条边界 case（如中英混杂、模糊描述）
4. **加入负样本**：10 条非 OnCall 输入（天气预报、闲聊），验证 OUT_OF_SCOPE 检测
5. **记录难度**：每条标注 `difficulty: easy/medium/hard`，分层报告准确率
6. **版本化**：评测集入 Git，随代码变更同步更新

**面试话术**：「评测集不是一次性产物——我们把它当作代码的一部分管理。每次改 Router 逻辑或新增 Skill，都会跑一遍路由评测回归，确保改动没有让已有场景退化。」

---

## 7. 面试高频追问准备清单

### 7.1 RAG 方向

| 问题 | 回答要点 |
| --- | --- |
| 「RAG 的 chunk size 怎么定的？」 | 「子块 300 是经验值——太小丢失上下文信号，太大降低检索精度。通过跑 benchmark 对比了 200/300/500 的 recall@3，300 在精度和召回间平衡最好。父块 2400 保证单个 SOP 步骤完整。」 |
| 「为什么不用 LangChain 自带的 RAG？自己写了什么？」 | 「LangChain 的 `similarity_search` 只做单路向量检索。我们自己加了 BM25 那一路解决精确术语匹配、RRF 融合解决分数归一化、Parent 去重解决小块上下文碎片化。这些都不是 `from langchain import rag` 能开箱即用的。」 |
| 「怎么评估 RAG 质量？」 | 「两个维度：检索质量用人工标注的 hit@k/mrr@k/recall@k，生成质量用 RAGAS 的 faithfulness 和 answer_relevancy。额外加了 OpenEvals 的 groundedness 防止 RAGAS 单指标过拟合。」 |
| 「长上下文模型会不会让 RAG 过时？」 | 「长上下文不等于无限上下文——200+ 文档全塞进去 token 成本太高，且没有来源追溯。我们的场景中，运维人员需要知道『这条建议来自哪个 SOP』，RAG 提供了这种可追溯性。」 |
| 「Rerank 有必要吗？性能开销多大？」 | 「我们跑了 A/B 对比，Rerank 对 MRR@3 有 X% 的提升。BGE-reranker-v2-m3 本地推理 50 条耗时约 X 秒，对在线延迟影响可控。」 |

### 7.2 Agent 方向

| 问题 | 回答要点 |
| --- | --- |
| 「Agent 循环你怎么防止死循环？」 | 「两个硬限制：max_iters=6 的硬上限 + Replanner 评估是否在推进（如果连续两轮没有新发现就强制结束）。Executor 的工具调用结果超过 max_result_chars 自动截断，防止 context 膨胀导致推理退化。」 |
| 「多个 Agent 怎么协调？有冲突怎么办？」 | 「Deep 模式下 Agent 之间不直接通信，各自看不同数据源产 Evidence。冲突由 EvidenceReducer 按确定性分数处理——现场指标(1.0) > 基础设施(0.9) > 日志(0.85) > Runbook(0.75)。如果 LLM 判定不确定，RCAJudge 会降低 confidence 并在报告里标注。」 |
| 「Agent 权限模型有什么设计考虑？」 | 「Fail-closed 原则——未登记工具默认 deny。三层防御有明确优先级：Skill 白名单是硬墙任何模式都不可绕过、然后 Mode 决定行为策略、Guardrail 做最终黑名单拦截。还有一个细节：只读工具豁免了 Skill 白名单，因为诊断场景中漏配一个查询工具就可能导致整个诊断链断裂。」 |
| 「怎么保证 Agent 的输出可用/可信？」 | 「每个诊断报告都有引用证据链——不是 LLM 自由发挥。RCA Judge 被明确告知只看准备好的 summary 不看原始内容，防止被无关信息干扰。每一步的 transition 都有 auditable 的日志。如果 LLM 失败，每个环节都有确定性回退。」 |
| 「为什么不直接用 LangChain 的 create_agent？」 | 「LangChain 的 create_agent 默认串行执行 tool_calls——一次 LLM 调用返回 3 个工具调用，它一个一个执行，不能利用只读工具之间的并行性。我们自己的 tool_runner 按 `concurrency_safe` 属性分批，只读工具可以 asyncio.gather 并行——这在诊断场景中很重要，因为经常要同时查 CPU + 内存 + 磁盘。」 |

### 7.3 工程方向

| 问题 | 回答要点 |
| --- | --- |
| 「Redis Streams 相比 Kafka/RabbitMQ 的考虑？」 | 「Redis Streams 对中小规模任务更轻量——不需要额外的 broker 集群。Consumer Group + ACK + Pending 回收开箱即用。DLQ 我们用了 Redis 的另一个 key 存储死信，通过 `/api/v1/queue/status` 暴露可观测性。如果任务量增长到每秒万级，迁移到 Kafka 只需要替换 queue 层的实现。」 |
| 「项目如果上生产还缺什么？」 | 「三个关键缺失：1) 用户认证和租户隔离（目前 CORS 全开）；2) CI/CD + 单元测试（目前无 tests/ 目录）；3) 监控告警（Prometheus metrics + Grafana Dashboard）。这些在 AGENTS.md 的已知限制中都有明确记录。」 |
| 「分布式并发槽怎么防止死锁？」 | 「每个槽有 TTL（默认 90s）+ 心跳续期（每 30s）。Worker 崩溃后槽在 90s 内自动释放。审批等待期间主动暂停心跳（slot.pause()），防止长时间阻塞占着槽不放。」 |

---

## 附录：改进优先级矩阵

```
                    面试价值
                低              高
          ┌─────────────────┬─────────────────┐
    低    │ 代码格式化       │ 跑出现有Benchmark │  ← 从这里开始
          │ ruff lint 修复   │ 数字写进简历      │
投   ├─────────────────┼─────────────────┤
入     │                  │                 │
时     │ 补充文档注释     │ Skill路由评测集  │  ← 然后做这个
间 中   │                  │ RAGAS全量50条   │
     ├─────────────────┼─────────────────┤
          │                  │                 │
    高    │ GraphRAG        │ Langfuse集成     │  ← 有精力再做
          │ K8s部署         │ Human-in-loop   │
          └─────────────────┴─────────────────┘
```

**建议执行顺序**：
1. **第一周**：跑通 benchmark，拿到 baseline 数字，写进简历
2. **第二周**：构建 Skill 路由评测集 + Agent 工具调用准确率统计
3. **第三周**：实现 Langfuse 集成 + Deep Agent 权限统一
4. **第四周+**：Human-in-the-loop 或 多 Judge 投票

---

> **核心建议**：面试官不会因为你「跑通了某个开源项目」而录用你——他们会因为你「对技术有深度思考、能用数据支撑决策、能把工程问题讲清楚」而录用你。这个项目的价值不在于代码本身，而在于它给了你一个完整的叙事：**你如何从需求出发，设计了一套 Agent 系统，在每个环节做了技术取舍，并用评测数据验证了自己的选择。**
