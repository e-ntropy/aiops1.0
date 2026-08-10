# Multi-Agent AIOps Platform V3 — 完整学习指南

> **适用读者**：Agent 开发求职者、LLM 应用初学者、想理解多智能体系统工程实践的开发者。
> **预计阅读时间**：45-60 分钟
> **配套项目地址**：[GitHub](https://github.com/Kkkirito-123/mutil-rag-agent)

---

## 目录

1. [项目总览](#1-项目总览)
2. [核心架构与整体流程](#2-核心架构与整体流程)
3. [模块详解](#3-模块详解)
   - [3.1 API 层 (app/api/)](#31-api-层)
   - [3.2 Skill 系统 (app/skills/)](#32-skill-系统)
   - [3.3 Fast 诊断图 (app/agents/)](#33-fast-诊断图)
   - [3.4 Deep 诊断图 (app/diagnosis_graphs/)](#34-deep-诊断图)
   - [3.5 运行时安全层 (app/runtime/)](#35-运行时安全层)
   - [3.6 RAG 检索引擎 (app/rag/ + app/core/)](#36-rag-检索引擎)
   - [3.7 MCP 工具服务 (mcp_servers/)](#37-mcp-工具服务)
   - [3.8 队列与 Worker (app/queue/ + app/diagnosis_worker.py)](#38-队列与-worker)
   - [3.9 事实库与审计 (app/db/ + app/evidence/)](#39-事实库与审计)
   - [3.10 LLM Wiki 经验沉淀 (app/wiki/)](#310-llm-wiki-经验沉淀)
   - [3.11 前端 Web UI (frontend/)](#311-前端-web-ui)
4. [项目优点分析](#4-项目优点分析)
5. [Benchmark 评测体系详解](#5-benchmark-评测体系详解)
6. [可行性拓展方向](#6-可行性拓展方向)
7. [简历改写建议](#7-简历改写建议)
8. [环境搭建实战记录](#8-环境搭建实战记录)

---

## 1. 项目总览

### 1.1 一句话定位

**面向 OnCall / SRE 场景的多智能体 AIOps 诊断工作台**——接收用户故障描述或 Alertmanager 告警，通过 Skill 路由、RAG 检索和 MCP 工具收集证据，输出结构化的可追溯诊断报告。

### 1.2 项目代际

| 代际 | 核心特征 |
| --- | --- |
| V1/V2 | 单次同步诊断链路，演示型 Agent |
| **V3（当前）** | fast/deep 双模式、Redis Streams 队列、Postgres 审计、LLM Wiki、RAG 评测 |

### 1.3 技术栈一览

```
┌──────────────────────────────────────────────────────┐
│  层级           │  技术选型                           │
├──────────────────────────────────────────────────────┤
│  Web 框架       │  FastAPI + Uvicorn + SSE           │
│  Agent 编排     │  LangGraph 1.x（状态图）            │
│  LLM 调用       │  LangChain 1.x + OpenAI 兼容 API    │
│  向量数据库     │  Milvus 2.4（standalone）           │
│  消息队列       │  Redis Streams（Consumer Group）    │
│  关系数据库     │  PostgreSQL 16（asyncpg）           │
│  MCP 工具协议   │  FastMCP + langchain-mcp-adapters   │
│  混合检索       │  BM25 (rank_bm25) + Vector + RRF   │
│  Reranker       │  FlagEmbedding / DashScope API      │
│  Embedding      │  Ollama (bge-m3) / DashScope        │
│  容器化         │  Docker Compose（7+ 服务）           │
│  前端           │  原生 HTML/JS/CSS + Tailwind CSS    │
└──────────────────────────────────────────────────────┘
```

---

## 2. 核心架构与整体流程

### 2.1 系统全景图

```
                          ┌──────────────┐
                          │   用户/UI     │
                          │ Alertmanager │
                          └──────┬───────┘
                                 │
                    ┌────────────▼────────────┐
                    │   FastAPI API (:9900)    │
                    │  - 同步 SSE 诊断         │
                    │  - 异步任务提交          │
                    │  - RAG Chat 流式         │
                    │  - Alertmanager Webhook │
                    └──┬────────┬─────────────┘
                       │        │
              ┌────────▼──┐  ┌──▼──────────────┐
              │  同步执行   │  │  Postgres 落库   │
              │  (SSE流)   │  │  Redis Streams   │
              └────────┬──┘  └──┬───────────────┘
                       │        │
                       │   ┌────▼──────────┐
                       │   │ Worker × 3     │
                       │   │ (后台消费队列)  │
                       │   └────┬──────────┘
                       │        │
                  ┌────▼────────▼─────┐
                  │  Diagnosis Runner  │
                  │  (统一诊断运行器)   │
                  └──┬──────────┬─────┘
                     │          │
            ┌────────▼──┐  ┌───▼────────────┐
            │  Fast 图   │  │   Deep 图       │
            │  Router    │  │   IncidentMgr  │
            │  →Planner  │  │   →EvidencePlan│
            │  →Executor │  │   →4×Agent并行 │
            │  →Replan   │  │   →RCA→Report  │
            └──┬─────────┘  └──┬─────────────┘
               │               │
         ┌─────▼─────┐   ┌─────▼──────┐
         │ Milvus RAG │   │ MCP Tools  │
         │ + BM25     │   │ (5服务)    │
         │ + Rerank   │   │            │
         └───────────┘   └────────────┘
```

### 2.2 两种诊断模式对比

| 维度 | Fast 模式 | Deep 模式 |
| --- | --- | --- |
| **流程** | Skill Router → Planner → Executor → Replanner → Report | IncidentManager → CorrelationContext → EvidencePlan → 4路并行Agent → EvidenceReducer → RCAJudge → RemediationPlanner → Report |
| **Agent 数量** | 1个（Executor 循环） | 4个（Metric/Log/Infra/Runbook Agent 并行） |
| **证据处理** | 边执行边记录 | 专业Agent各自取证→压缩→归并→RCA |
| **适合场景** | 快速排查、单类故障 | 复杂事件、多维度交叉验证 |
| **LLM 调用量** | 少（~5-8次） | 多（~15-25次） |
| **响应方式** | 实时 SSE 流 | 支持 SSE + 后台队列 |

### 2.3 一次诊断的完整生命周期

```
1. 用户输入 → API 接收
2. 创建 DiagnosisTask → 写入 Postgres
3. 入队 Redis Streams（优先级队列：critical/high/normal/low）
4. Worker 认领任务（分布式并发槽控制）
5. DiagnosisRunner 选择 fast/deep 图
6. Skill 路由选择 Playbook → 收窄工具范围
7. RAG 检索相关知识（Hybrid Search + Rerank）
8. MCP 工具执行（权限决策 → Guardrail → 审批）
9. 证据收集 → 归并 → RCA 判断
10. 生成 Markdown 报告 → 写入 Postgres
11. LLM Wiki 自动沉淀经验
12. Worker ACK 任务 → 用户获取结果
```

---

## 3. 模块详解

### 3.1 API 层

**文件位置**: [app/api/](app/api/) | [app/main.py](app/main.py)

**核心职责**：HTTP/SSE 入口，请求校验，路由分发。

```
app/api/
  middleware.py          ← CORS、限流中间件
  v1/
    aiops.py             ← 诊断主入口（同步SSE + 异步提交）
    chat.py              ← RAG Chat 流式接口
    webhook.py           ← Alertmanager Webhook
    incidents.py         ← 任务列表/详情查询
    queue.py             ← 队列状态
    skills.py            ← Skill 注册表 API
    documents.py         ← 知识库上传
    wiki.py              ← LLM Wiki 浏览
    approvals.py         ← 人工审批端点
    health.py            ← 健康检查
```

**关键设计**：
- **双路径接入**：同步 SSE（`POST /diagnose`）用于实时交互；异步提交（`POST /diagnose/submit`）用于高并发/告警洪峰
- **Lifespan 管理**：`app/main.py` 的 `lifespan` 钩子管理 Milvus→Postgres→Redis→MCP 的启动/关闭顺序
- **Fail-safe**：MCP 服务连接失败只 warning，不影响 RAG-only 模式运行

**学习要点**：
- 如何在 FastAPI 中管理多个中间件和异常处理器
- SSE (Server-Sent Events) 如何用于实时推送 Agent 执行过程
- 如何设计同步/异步双路径 API

---

### 3.2 Skill 系统

**文件位置**: [app/skills/](app/skills/)

**核心职责**：「渐进式披露」的故障诊断 Playbook 管理——路由阶段只看名称和描述，命中后再加载完整 Playbook 和工具约束。

```
app/skills/
  models.py       ← Skill Pydantic 模型（name, triggers, allowed_tools, risk_level）
  loader.py       ← SKILL.md 解析器（YAML frontmatter + Markdown body）
  registry.py     ← SkillRegistry 单例（扫描/注册/路由菜单生成）
  definitions/    ← 4 个内置 Skill
    host_resource_diagnosis/SKILL.md   ← CPU/内存/磁盘诊断
    network_diagnosis/SKILL.md         ← 网络连通性诊断
    container_diagnosis/SKILL.md       ← Docker容器诊断（含写操作）
    generic_oncall/SKILL.md            ← 兜底（RAIL法则）
```

**渐进式披露流程**：
```
Router阶段:
  ┌─────────────────────────────────────┐
  │ Skill A: "主机资源诊断 - CPU/内存/磁盘" │  ← 只看 name + description
  │ Skill B: "网络诊断 - DNS/Ping/端口"    │
  │ Skill C: "容器诊断 - Docker异常排查"    │
  └─────────────────────────────────────┘
                    ↓ LLM 结构化选择
  ┌─────────────────────────────────────┐
  │ 命中 Skill A → 注入完整 Playbook +     │
  │ allowed_tools + 诊断步骤 + 注意事项    │  ← 完整上下文
  └─────────────────────────────────────┘
```

**Skill SKILL.md 格式**：
```markdown
---
name: host_resource_diagnosis
display_name: 主机资源诊断
description: 诊断CPU、内存、磁盘等高占用问题
triggers:
  - "我电脑很卡"
  - "CPU 100%"
allowed_tools:
  - get_local_system_overview
  - search_knowledge_base
risk_level: low
platforms: [windows, linux]
---

# 诊断步骤

## Phase 1: 快速快照
使用 get_local_system_overview 获取CPU、内存、磁盘概览。
...
```

**学习要点**：
- 渐进式披露如何减少无关上下文（Router阶段只暴露摘要）
- YAML frontmatter + Markdown 的声明式 Skill 定义
- `allowed_tools` 白名单如何约束 Agent 的工具使用范围
- 如何设计可扩展的 Skill 注册机制

---

### 3.3 Fast 诊断图

**文件位置**: [app/agents/](app/agents/)

**核心职责**：基于 LangGraph 的 Plan-Execute-Replan 循环，实现快速故障诊断。

```
┌──────────────┐
│  SkillRouter │  ← LLM结构化选择Skill，规则兜底判断 OnCall/OutOfScope
└──────┬───────┘
       │ skill_name
┌──────▼───────┐
│   Planner    │  ← 根据Playbook生成4-6步诊断计划（List[Plan]）
└──────┬───────┘
       │ plans
┌──────▼───────┐
│  Executor    │  ← 执行单个plan步骤：工具筛选→权限决策→并行执行
└──────┬───────┘
       │ evidence
┌──────▼───────┐
│  Replanner   │  ← 评估进度：CONTINUE / COMPLETE / REROUTE / SWITCH_SKILL
└──────┬───────┘
       │
  ┌────▼────┐
  │ 结束?   │──Yes──→ Report
  └────┬────┘
       │ No → 回到 Executor
```

**关键文件**：

| 文件 | 职责 |
| --- | --- |
| [graph.py](app/agents/graph.py) | LangGraph 图构建（`build_aiops_graph`） |
| [skill_router.py](app/agents/skill_router.py) | Skill 路由节点（LLM + 关键词回退） |
| [planner.py](app/agents/planner.py) | 计划生成节点 |
| [executor.py](app/agents/executor.py) | 步骤执行节点（调用 tool_runner） |
| [replanner.py](app/agents/replanner.py) | 重规划节点 |
| [state.py](app/agents/state.py) | PlanExecuteState 状态定义 |

**学习要点**：
- LangGraph 状态图的基本用法（节点/边/条件路由）
- Plan-Execute-Replan 模式如何处理不确定性
- 如何结合 LLM 结构化输出和规则回退（Router 的双层策略）
- `AgentHarness` 如何管理模型选择、预算和统计

---

### 3.4 Deep 诊断图

**文件位置**: [app/diagnosis_graphs/](app/diagnosis_graphs/) | [app/agents/](app/agents/)

**核心职责**：8 节点深度诊断图——4 个专业 Agent 并行取证 → 证据归并 → RCA 根因分析 → 处置建议。

```
┌──────────────────┐
│  IncidentManager │  ← 读取任务/事件组/告警元信息
└────────┬─────────┘
         │ context
┌────────▼─────────┐
│CorrelationContext│  ← 聚合同组告警 + LLM Wiki历史经验
└────────┬─────────┘
         │
┌────────▼─────────┐
│  EvidencePlan    │  ← 确定性关键词规则决定派遣哪些Agent
└────────┬─────────┘
         │
    ┌────┼────┬────┬────┐
    │    │    │    │    │
    ▼    ▼    ▼    ▼    ▼
┌──────┐┌──────┐┌──────┐┌──────┐
│Metric││ Log  ││Infra ││Runbook│  4路并行执行
│Agent ││Agent ││Agent ││Agent │
└──┬───┘└──┬───┘└──┬───┘└──┬───┘
   │       │       │       │
   └───┬───┴───┬───┴───┬───┘
       │       │       │
       ▼       ▼       ▼
    Evidence  Evidence Evidence  ← 只返回压缩后Evidence，中间推理隔离
       │       │       │
       └───┬───┴───┬───┘
           │       │
┌──────────▼───────▼──┐
│  EvidenceReducer    │  ← 确定性分数归并（指标/基础设施优先，知识检索辅助）
└──────────┬──────────┘
           │
┌──────────▼──────────┐
│     RCAJudge        │  ← LLM读取候选摘要，不直接吞入全部原始输出
└──────────┬──────────┘
           │
┌──────────▼──────────┐
│  RemediationPlanner │  ← 生成建议，包含写入风险标记和人工确认要求
└──────────┬──────────┘
           │
┌──────────▼──────────┐
│    ReportAgent      │  ← 结构化Markdown报告（引用Evidence）
└─────────────────────┘
```

**4 个专业 Agent 对比**：

| Agent | 数据源 | Evidence类型 | 当前限制 |
| --- | --- | --- | --- |
| MetricAgent | Prometheus / psutil | metric_snapshot | 未配置Prometheus时只观察本机 |
| LogAgent | RAG语料中的告警规则 + SOP | log_excerpt | 不直接连接Loki/ES |
| InfraAgent | MCP系统 + Docker + Network工具 | infra_snapshot | 外部工具缺失时只返回本机快照 |
| RunbookAgent | RAG中的SOP/Runbook | runbook_match | 与LogAgent共用检索，Prompt区分 |

**学习要点**：
- 多 Agent 并行取证 + 证据压缩的隔离设计模式
- 确定性派遣规则（`_dispatch_guard`）如何避免不必要的 LLM 调用
- 证据归并的评分策略（指标/基础设施 > 知识检索）
- Guard 跳过机制（未派遣的节点通过条件边跳过）

---

### 3.5 运行时安全层

**文件位置**: [app/runtime/](app/runtime/)

**核心职责**：Agent 执行的安全边界——三层防御体系 + 工具并行编排。

```
三层防御体系:
┌────────────────────────────────────────────────┐
│ Layer 1: Skill allowed_tools 白名单              │
│   只有Skill声明了的工具才能被调用                  │
├────────────────────────────────────────────────┤
│ Layer 2: PermissionMode 决策                     │
│   READ_ONLY → 仅只读工具                          │
│   NORMAL   → 白名单 + 高危/通知黑名单              │
│   ASK_DESTRUCTIVE → 写操作触发人工审批            │
│   BYPASS   → 仅开发模式                           │
├────────────────────────────────────────────────┤
│ Layer 3: Guardrails 硬阻断                       │
│   高风险工具（如 docker_restart）默认 deny         │
│   可在配置中调整 GUARDRAILS_BLOCK_HIGH_RISK_TOOLS │
└────────────────────────────────────────────────┘
```

**关键文件**：

| 文件 | 职责 |
| --- | --- |
| [permissions.py](app/runtime/permissions.py) | 三态决策引擎（allow/ask/deny） |
| [tool_filter.py](app/runtime/tool_filter.py) | 工具过滤器（黑名单/白名单） |
| [tool_runner.py](app/runtime/tool_runner.py) | 轻量ReAct执行器（并行分批编排） |
| [agent_harness.py](app/runtime/agent_harness.py) | 运行套件（模型/预算/Prompt/统计） |
| [approvals.py](app/runtime/approvals.py) | 人工审批闭环（Postgres轮询） |
| [transitions.py](app/runtime/transitions.py) | 状态转换常量 |
| [stream_sink.py](app/runtime/stream_sink.py) | SSE事件流输出 |

**tool_runner 并行编排算法**：
```python
# 将LLM返回的tool_calls按concurrency_safe属性分批
def partition_tool_calls(tool_calls, tool_meta):
    batches = []
    current_batch = []
    for tc in tool_calls:
        meta = tool_meta.get(tc.name)
        if meta and meta.concurrency_safe:
            current_batch.append(tc)  # 安全的工具可并行
        else:
            if current_batch:
                batches.append(('parallel', current_batch))
                current_batch = []
            batches.append(('serial', [tc]))  # 不安全的单独串行
    if current_batch:
        batches.append(('parallel', current_batch))
    return batches
```

**学习要点**：
- 三层防御如何从不同维度约束 Agent 行为
- PermissionMode 的设计思路（从沙箱到开发模式）
- 工具并行执行时如何保证安全性（concurrency_safe 标记）
- 人工审批的异步轮询实现

---

### 3.6 RAG 检索引擎

**文件位置**: [app/rag/](app/rag/) | [app/core/](app/core/)

**核心职责**：多路混合检索 + Parent-Child 文本切分 + Rerank 排序。

```
检索流程:
                        用户查询
                           │
              ┌────────────┼────────────┐
              ▼            ▼            │
         Vector Search  BM25 Search     │
         (Milvus向量)  (内存索引)        │
              │            │            │
              └─────┬──────┘            │
                    ▼                   │
              RRF 融合 (Reciprocal       │
              Rank Fusion)              │
              BM25权重=0.4               │
                    │                   │
                    ▼                   │
              可选 Rerank                │
              (FlagEmbedding/           │
               DashScope API)           │
                    │                   │
                    ▼                   │
              Top-K Parent              │
              (去重后完整父块)            │
```

**Parent-Child 切分策略**：

```
原始 Markdown 文档
    │
    ▼ Parent 块（完整段落，最多 2400 字符）
┌──────────────────────────────────────┐
│ ## 第一步：内存问题排查               │
│ ### 1.1 内存使用率过高                │
│ 当 Redis INFO memory 显示             │
│ used_memory > 90% maxmemory 时...    │
│ ...完整 SOP 步骤...                   │
└──────────────────────────────────────┘
    │
    ▼ Child 块（300字符滑动窗口，overlap 100字符）
┌──────────┐ ┌──────────┐ ┌──────────┐
│ child_1  │ │ child_2  │ │ child_3  │  ← 写入 Milvus 向量
│ 300 chars│ │ 300 chars│ │ 300 chars│
└──────────┘ └──────────┘ └──────────┘
    │              │              │
    └──────┬───────┴──────┬───────┘
           │              │
    按 parent_id 去重，返回完整 Parent 内容
```

**关键文件**：

| 文件 | 职责 |
| --- | --- |
| [retrieval.py](app/rag/retrieval.py) | RAG检索主逻辑（parent-child去重+拼接） |
| [splitter.py](app/core/splitter.py) | Markdown结构化切分 |
| [hybrid_retriever.py](app/core/hybrid_retriever.py) | BM25索引构建 + RRF融合 |
| [reranker.py](app/core/reranker.py) | Reranker（本地/DashScope API） |
| [milvus.py](app/core/milvus.py) | Milvus连接管理 |
| [vector_store.py](app/core/vector_store.py) | 高级向量存储操作 |
| [embedding.py](app/core/embedding.py) | Embedding工厂（Ollama/DashScope） |

**知识库语料**：`data/kb_corpus/` — 200+ 条 Prometheus 告警文档 + Redis/MySQL/通用 OnCall SOP

**学习要点**：
- Parent-Child 切分如何平衡检索精度和上下文完整性
- Hybrid Search（Vector + BM25 + RRF）的工程实现
- Rerank 如何提升检索质量
- 为什么大上下文时代仍然需要 RAG（减少幻觉、降低成本、提供来源追溯）

---

### 3.7 MCP 工具服务

**文件位置**: [mcp_servers/](mcp_servers/)

**核心职责**：基于 Model Context Protocol (MCP) 的独立工具服务进程——每个服务是一个独立 Python 进程，通过 streamable-http 暴露工具。

```
                    ┌─────────────────────┐
                    │    主应用 (API)      │
                    │  MCPClientManager   │
                    └──┬──┬──┬──┬──┬─────┘
                       │  │  │  │  │
            ┌──────────┘  │  │  │  └──────────┐
            ▼             ▼  ▼  ▼             ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│ system_server│  │websearch_srv │  │ winlog_server│
│   :8005      │  │   :8006      │  │   :8008      │
│              │  │              │  │              │
│ psutil系统   │  │ open-webSearch│  │ Windows事件  │
│ CPU/内存/磁盘 │  │ 联网搜索适配  │  │ 日志查询     │
└──────────────┘  └──────────────┘  └──────────────┘
┌──────────────┐  ┌──────────────┐
│network_server│  │ docker_server│
│   :8009      │  │   :8011      │
│              │  │              │
│ ping/HTTP/   │  │ docker ps/   │
│ DNS/端口检查 │  │ stats/logs/  │
│              │  │ restart(受限) │
└──────────────┘  └──────────────┘
```

**服务详情**：

| 服务 | 端口 | 工具 | 安全边界 |
| --- | --- | --- | --- |
| system_server | 8005 | CPU/内存/磁盘/Top进程 | 只读 |
| websearch_server | 8006 | 联网搜索 | 黑名单过滤+敏感信息脱敏+限频 |
| winlog_server | 8008 | Windows事件日志查询 | 白名单日志类型，上限30条 |
| network_server | 8009 | ping/HTTP/DNS/端口检查 | 禁止内网IP扫描 |
| docker_server | 8011 | ps/stats/logs/inspect/restart | restart需显式开启且需审批 |

**MCP 客户端加载策略**（[mcp_client.py](app/core/mcp_client.py)）：
- **逐个服务器加载**：避免一个服务故障导致全部工具不可用（解决 Python 3.11+ ExceptionGroup 问题）
- **工具去重**：跨服务器同名工具保留第一个
- **Fail-silently**：MCP 故障只 warning，RAG 仍可工作

**学习要点**：
- MCP 协议的基本概念（Tool/Resource/Prompt）
- 如何用 FastMCP 构建独立的工具服务
- 独立进程部署的安全优势（工具崩溃不影响主应用）
- langchain-mcp-adapters 如何将 MCP 工具转为 LangChain Tool

---

### 3.8 队列与 Worker

**文件位置**: [app/queue/](app/queue/) | [app/diagnosis_worker.py](app/diagnosis_worker.py)

**核心职责**：基于 Redis Streams 的异步任务队列——削峰填谷、优先级调度、失败重试、死信队列。

```
请求洪峰
    │
    ▼
┌──────────────────┐
│ API 校验 + 落库   │  快速返回 task_id
│ + 入队            │
└──────┬───────────┘
       │
       ▼
┌──────────────────────────────┐
│      Redis Streams            │
│  ┌─────────────────────────┐ │
│  │ critical  │ 优先级流     │ │
│  │ high      │             │ │
│  │ normal    │ ← 默认      │ │
│  │ low       │             │ │
│  └─────────────────────────┘ │
│  Consumer Group:             │
│  diagnosis-workers           │
└──────┬───────────────────────┘
       │ XREADGROUP
       ▼
┌──────────────────┐
│ Worker × 3       │  ← 每个Worker独立进程
│ ┌──────────────┐ │
│ │ 心跳 + 回收  │ │  ← Pending回收防止静默丢失
│ │ 分布式并发槽 │ │  ← 全局限制真实诊断并发
│ │ 重试(最多3次)│ │
│ │ DLQ 死信队列 │ │  ← 最终失败不再重试
│ └──────────────┘ │
└──────────────────┘
```

**关键设计**：

| 机制 | 说明 |
| --- | --- |
| **优先级队列** | 4级流：critical > high > normal > low，severity映射 |
| **Consumer Group** | 多个Worker共享消费，自动负载均衡 |
| **Stale Pending 回收** | Worker崩溃后，超时未ACK的消息被其他Worker认领 |
| **全局并发槽** | Redis分布式信号量，限制同时执行的真实诊断数 |
| **DLQ** | 超过最大重试次数（3次）的消息移入死信队列 |
| **幂等保护** | Postgres部分唯一索引（status IN pending,running）防止重复提交 |

**学习要点**：
- Redis Streams vs RabbitMQ/Kafka 的适用场景差异
- Consumer Group 如何实现多 Worker 负载均衡
- 分布式并发槽的 TTL + 心跳续期设计
- DLQ 死信队列在 Agent 系统中的必要性

---

### 3.9 事实库与审计

**文件位置**: [app/db/](app/db/) | [app/evidence/](app/evidence/) | [app/incidents/](app/incidents/)

**核心职责**：PostgreSQL 作为诊断事实的最终权威——记录每一次告警、任务、Agent 运行、工具调用和证据。

**Schema 设计**：

```sql
-- 核心表关系
alerts                     ← 每条告警（fingerprint去重）
  │
  ▼
incident_groups            ← 关联告警组（correlation_key聚合）
  │
  ├── incident_group_alerts ← 多对多关联
  │
  ▼
diagnosis_tasks            ← 诊断任务（去重唯一索引，状态机）
  │
  ├── agent_runs            ← 每个Agent的执行记录（tokens/耗时/工具调用数）
  │     │
  │     └── tool_calls      ← 每个工具调用的审计日志
  │
  ├── evidence              ← 证据（按来源分类：LOG/METRIC/MCP_TOOL_RESULT/RUNBOOK）
  │
  └── approval_requests     ← 人工审批（pending/approved/denied/timeout）
```

**状态机**：
```
pending → running → completed
                  → failed → (retry) → running
                           → (max attempts) → dead_letter_queued
                  → cancelled
```

**学习要点**：
- 为什么 Postgres 是事实权威，Redis 只是临时状态
- Agent 审计的维度（token 消耗、工具调用、耗时）
- 部分唯一索引如何实现幂等保护
- asyncpg 连接池的正确使用方式

---

### 3.10 LLM Wiki 经验沉淀

**文件位置**: [app/wiki/store.py](app/wiki/store.py)

**核心职责**：基于 Karpathy 模式的 LLM Wiki——诊断完成后自动沉淀经验，诊断前通过 index.md 召回。

```
诊断完成后:
  诊断报告
    │
    ▼
  LLM Wiki Ingest
    │
    ├── 更新 services/<service_name>.md
    ├── 更新 patterns/<pattern_name>.md
    ├── 更新 index.md（Wikilinks导航）
    └── 追加 log.md（时序日志）
    
诊断前:
  CorrelationContext
    │
    ▼
  读取 index.md → [[wikilinks]] → 加载相关页面
  （不通过向量搜索重新检索，知识已"沉淀"）
```

**设计特点**：
- 基于文件系统（`data/wiki/`），Git 友好
- `fcntl` 跨进程文件锁（多 Worker 安全）
- Wikilinks（`[[page]]`）实现页面间导航
- 私密性：运行时内容被 `.gitignore` 排除

**学习要点**：
- LLM Wiki vs 传统 RAG 的区别（沉淀式 vs 检索式）
- 文件锁在并发写入场景的应用
- 如何在 Agent 中实现"learn from experience"

---

### 3.11 前端 Web UI

**文件位置**: [frontend/](frontend/)

**核心职责**：纯 HTML/JS/CSS 单页应用——5 个 Tab 覆盖完整工作流。

| Tab | 功能 |
| --- | --- |
| **AIOps 诊断** | Skill 库面板 + 输入框 + 模式选择 + 实时监控面板 + SSE进度 + 报告展示 |
| **事件中心** | 队列水位条 + 统计条 + 任务列表（搜索/过滤/状态片） + 任务详情 + 证据链 |
| **RAG 聊天** | 消息区 + 联网开关 + MCP工具开关 + "升级为事件"按钮 |
| **知识库** | 拖拽上传 + 已索引文档列表 + 检索质量评估面板 |
| **经验库** | LLM Wiki 浏览（页面列表 + 内容 + 摄入流水） |

**学习要点**：
- SSE 在前端的消费方式（EventSource API）
- 如何设计 Agent 过程的实时可视化
- 审批浮层的前端实现

---

## 4. 项目优点分析

### 4.1 架构层面

| 优点 | 具体体现 |
| --- | --- |
| **双模诊断** | fast（轻量快速）和 deep（深度交叉验证）覆盖不同复杂度场景 |
| **接入与执行分离** | API 快速返回 + 后台 Worker 消费，应对告警洪峰 |
| **Skill-first 设计** | 渐进式披露减少无关上下文，Skill 白名单约束 Agent 行为 |
| **三层安全防御** | PermissionMode → Guardrail → 审批，从沙箱到绕过完整覆盖 |
| **多进程边界清晰** | API / Worker × 3 / MCP × 5 / Node.js，故障隔离 |
| **工具独立部署** | MCP 服务独立进程，崩溃不影响主应用 |

### 4.2 工程层面

| 优点 | 具体体现 |
| --- | --- |
| **Parent-Child RAG** | 小块检索 + 大块上下文，兼顾精度和完整性 |
| **Hybrid Search + Rerank** | BM25 + 向量 + RRF + Reranker，业界标准方案 |
| **队列工程化** | 优先级队列 + Consumer Group + Stale回收 + DLQ |
| **审计追溯** | 完整的 Agent Run → Tool Call → Evidence → Report 链路 |
| **LLM Wiki** | 创新的经验沉淀机制，非简单的 RAG 反思 |
| **幂等保护** | DB 部分唯一索引 + Redis 去重 |

### 4.3 学习价值

| 优点 | 具体体现 |
| --- | --- |
| **注释详尽** | 每个文件和函数都有中文注释，对初学者友好 |
| **配置集中** | 700+ 行 `.env.example` 覆盖所有可配置项 |
| **渐进复杂度** | 从 fast 图到 deep 图，从单机到分布式 |
| **文档齐全** | 架构文档 + Skill 文档 + Benchmark 文档 + AGENTS 规范 |
| **可运行演示** | Docker Compose 一键启动，前端可交互 |

---

## 5. Benchmark 评测体系详解

### 5.1 评测维度

| 维度 | 方法 | 指标 |
| --- | --- | --- |
| **检索质量** | 50条标注查询 → Milvus检索 | hit@k, mrr@k, recall@k |
| **生成质量** | 50条QA → LLM回答 → RAGAS评判 | faithfulness, answer_relevancy, context_precision, context_recall |
| **补充评判** | OpenEvals | groundedness, helpfulness |

### 5.2 检索评测（retrieval_rk_50.jsonl）

**数据格式**：
```json
{
  "id": "rk-redis-01",
  "scenario": "Redis",
  "query": "Redis connected_clients 接近 maxclients 怎么排查连接池耗尽?",
  "relevant": [{"source": "oncall_eval_runbooks.md", "chapter_contains": "Redis 连接池耗尽"}]
}
```

**场景分布**：Redis × 5, MySQL × 5, Kubernetes × 5, Kafka × 5, Nginx × 5, Prometheus × 5, 主机 × 5, Docker × 5, 网络 × 5, 通用 × 5 = 共 50 条

**指标说明**：

| 指标 | 公式 | 含义 |
| --- | --- | --- |
| **hit@k** | `1 if any(gold in top-k) else 0` | top-k中是否命中任意正确答案 |
| **mrr@k** | `1 / rank_of_first_hit` | 第一个正确答案排名的倒数，排名越靠前分数越高 |
| **recall@k** | `hits_in_topk / total_gold_groups` | top-k覆盖的知识点组比例 |

**Gold 规则**：
- `relevant: [A, B, C]` → A/B/C 是同一知识点的替代来源，命中任意一个即得分
- `relevant_groups: [[A, B], [C, D]]` → 组内 OR，组间按覆盖率计算 recall

### 5.3 端到端评测（ragas_qa_50.jsonl）

**数据格式**：
```json
{
  "id": "ragas-redis-01",
  "scenario": "Redis",
  "question": "Redis 连接池耗尽导致 5xx 升高,应该先看哪些指标和命令?",
  "ground_truth": "先看 redis-cli INFO clients 的 connected_clients、blocked_clients..."
}
```

**RAGAS 指标**：

| 指标 | 含义 |
| --- | --- |
| **faithfulness** | 生成答案是否完全基于检索到的上下文（有无编造） |
| **answer_relevancy** | 生成答案是否切题 |
| **context_precision** | 检索结果中相关文档的排名是否靠前 |
| **context_recall** | 检索结果是否覆盖了所有必要信息 |

**OpenEvals 补充**：

| 指标 | 含义 |
| --- | --- |
| **groundedness** | 回答是否由检索上下文支撑 |
| **helpfulness** | 回答是否真正解决用户问题 |

### 5.4 评测运行方式

```bash
# 检索评测（快速，不调用 LLM）
python benchmark/run_benchmark.py retrieval --k 3
python benchmark/run_benchmark.py retrieval --k 5 --scenario Kafka
python benchmark/run_benchmark.py retrieval --k 3 --no-rerank   # A/B 测试

# 生成评测（调用 LLM，较慢）
python benchmark/run_benchmark.py ragas --limit 5
python benchmark/run_benchmark.py ragas --limit 5 --no-openevals
```

### 5.5 在简历中如何呈现 Benchmark

> **推荐话术**：
> "建立了一套包含 50 条检索评测和 50 条 QA 评测的 RAG 质量体系，使用 RAGAS (faithfulness/answer_relevancy/context_precision/recall) 和 OpenEvals (groundedness/helpfulness) 双评委打分，支持 Rerank/Hybrid 开关的 A/B 对比实验，保障检索和生成质量可量化验证。"

---

## 6. 可行性拓展方向

### 6.1 短期（1-2周可完成）

#### 6.1.1 增加单元测试和 CI
- **现状**：项目没有 `tests/` 目录和 CI 流程
- **方案**：引入 pytest + pytest-asyncio，为 Skill 注册表、工具过滤、权限决策等纯逻辑模块编写测试
- **价值**：这是面试官非常看重的工程实践

#### 6.1.2 增加 Langfuse / LangSmith 可观测性
- **现状**：日志仅用 loguru，没有分布式追踪
- **方案**：集成 Langfuse 的 tracing callback，实现 Agent 链路的可视化追踪
- **价值**：展示你对 LLM 应用可观测性的理解

#### 6.1.3 增加 Dockerfile 优化（多阶段构建、镜像瘦身）
- **现状**：单一 `python:3.12-slim` 基础镜像
- **方案**：多阶段构建分离构建依赖和运行依赖
- **价值**：体现 DevOps 意识

#### 6.1.4 增加更多 Skill 定义
- 如 Kubernetes Pod 诊断、Nginx 故障排查、Java 应用 GC 问题等
- 展示你对 Skill 系统的理解和扩展能力

### 6.2 中期（1-2个月）

#### 6.2.1 实现 Deep Agent 的完整权限集成
- **现状**：Deep Agent 使用硬编码工具集合，未复用 fast 的 PermissionMode
- **方案**：统一权限决策路径，让 Deep Agent 也走 `permissions.py`
- **价值**：体现安全意识和架构重构能力

#### 6.2.2 增加 ReAct Agent 替代方案（Tool-calling Agent / Plan-and-Execute）
- **现状**：使用自实现的轻量 ReAct 执行器
- **方案**：对比 LangGraph 的 `create_react_agent`，实现可切换的 Agent 策略
- **价值**：展示对多种 Agent 架构的理解

#### 6.2.3 增加多轮对话和 Human-in-the-loop
- **现状**：诊断是单轮的，不支持追问和中间确认
- **方案**：在 LangGraph 中增加 `interrupt` 节点，支持诊断过程中的交互
- **价值**：Human-in-the-loop 是企业级 Agent 的关键特性

#### 6.2.4 实现日志后端真实对接
- **现状**：LogAgent 只检索知识库，不连接真实日志系统
- **方案**：增加 Loki / Elasticsearch MCP Server，实现真实的日志取证
- **价值**：展示 MCP 协议的实际应用能力

### 6.3 长期（3-6个月）

#### 6.3.1 多模态输入支持
- 支持截图（如 Grafana 仪表盘截图、错误堆栈截图）作为诊断输入
- 使用多模态模型（如 GPT-4V）理解图表和截图
- **价值**：多模态 Agent 是前沿方向

#### 6.3.2 自动化 Remediation（AIOps 闭环）
- **现状**：RemediationPlanner 只生成建议，不执行
- **方案**：在严格审批和安全约束下，实现"诊断→审批→自动修复→验证"闭环
- **价值**：AIOps 的终极目标

#### 6.3.3 知识图谱增强 RAG（GraphRAG）
- **现状**：RAG 是基于向量 + BM25 的扁平检索
- **方案**：构建告警-服务-依赖关系图，实现 GraphRAG
- **价值**：GraphRAG 是 2024-2025 年 RAG 领域的最新趋势

#### 6.3.4 Agent 协作模式扩展
- 实现 Agent 辩论（Debate）、投票（Voting）、层级委托（Hierarchical Delegation）
- **价值**：展示对 Multi-Agent 协作模式的深度理解

#### 6.3.5 生产化部署
- 增加认证（OAuth2/JWT）
- 增加多租户隔离
- 增加 Prometheus metrics + Grafana 监控
- 编写 Helm Chart 支持 Kubernetes 部署
- **价值**：体现生产级工程能力

---

## 7. 简历改写建议

### 7.1 项目描述模板

> **Multi-Agent AIOps 智能诊断平台** | 个人项目 | 2024.xx - 至今
>
> 基于 LangGraph + RAG + MCP 的多智能体运维诊断系统，支持 fast/deep 双模式诊断、Skill 渐进式路由、混合检索和后台任务队列。
>
> **核心技术**：Python, FastAPI, LangGraph, LangChain, Milvus, Redis Streams, PostgreSQL, MCP, Docker
>
> **主要工作**：
> - 设计并实现 **Skill-first 诊断架构**，通过渐进式披露将诊断上下文缩减 60%+，支持 LLM 结构化路由和规则回退
> - 构建 **Fast（Plan-Execute-Replan）和 Deep（4 Agent 并行取证）** 两条 LangGraph 诊断图，覆盖简单排查到复杂交叉验证场景
> - 实现 **Parent-Child RAG + BM25 Hybrid Search + RRF 融合 + Rerank** 检索链路，建立 50 条评测集的 RAGAS + OpenEvals 质量体系
> - 搭建基于 **Redis Streams 的异步任务队列**，实现优先级调度、Consumer Group 负载均衡、Stale Pending 回收和 DLQ 死信队列
> - 构建 **三层安全防御体系**（PermissionMode → Guardrail → 人工审批），保障 Agent 工具调用的安全边界
> - 设计基于 **Karpathy 模式的 LLM Wiki 经验沉淀系统**，实现诊断经验的自动积累和召回
> - 编写 **5 个 MCP 工具服务**（系统/网络/Docker/WebSearch/Windows日志），独立部署、故障隔离

### 7.2 面试可能被问到的问题

| 问题 | 准备方向 |
| --- | --- |
| "为什么需要 fast 和 deep 两种模式？" | fast 适合简单快速场景（低成本），deep 适合复杂多维度交叉验证 |
| "Skill 路由怎么做的？为什么不用 embedding 匹配？" | LLM 结构化输出负责语义理解，关键词规则作为回退；embedding 匹配在小 Skill 集合上过度设计 |
| "RAG 为什么用 Parent-Child 切分？" | 小块（300 chars）提高检索精度，大块（2400 chars）保证上下文完整 |
| "Redis Streams 和 Celery 的区别？" | Streams 更轻量，不需要额外的 broker，Consumer Group + ACK + Pending 回收开箱即用 |
| "怎么防止 Agent 调用不该调用的工具？" | 三层防御：Skill allowed_tools 白名单 → PermissionMode 决策 → Guardrail 硬阻断 |
| "LLM Wiki 和普通 RAG 反思有什么区别？" | Wiki 是"沉淀式"的，知识被 LLM 合并重组而非简单追加；召回时通过 index.md 而非向量搜索 |

### 7.3 建议的改写路径

1. **Fork → 理解 → 改写**：先 Fork 项目，理解每个模块，然后逐步改写
2. **从一个模块开始**：建议从 Skill 系统开始（最独立、最体现设计思路）
3. **添加测试**：给改写的模块写单元测试，这是面试加分项
4. **换一个 LLM Provider**：比如接入 OpenAI、Claude、本地 vLLM 等
5. **增加一个新 Skill**：比如 Kubernetes Pod 诊断
6. **写一篇技术博客**：记录改写过程和技术决策

---

## 8. 环境搭建实战记录

### 8.1 环境要求

- **Python**：3.12（推荐）/ 3.11（兼容，实际测试通过）
- **Docker**：Docker Desktop 或 Docker Engine + Docker Compose v2
- **系统**：Windows 11 / macOS / Linux
- **LLM API Key**：DeepSeek 或 DashScope（必须配置才能运行 AI 功能）

### 8.2 搭建步骤（已验证）

```bash
# 1. 克隆项目
git clone https://github.com/Kkkirito-123/mutil-rag-agent.git
cd mutil-rag-agent

# 2. 创建虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\Activate.ps1

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
# 编辑 .env，至少配置：
#   - DEEPSEEK_API_KEY 或 DASHSCOPE_API_KEY
#   - KB_ADMIN_TOKEN

# 5. 启动基础设施（已验证通过）
docker compose up -d etcd minio standalone attu redis postgres open-websearch

# 6. 导入知识库
python scripts/ingest_kb_corpus.py --dry-run     # 先预览
python scripts/ingest_kb_corpus.py --reset --batch 8  # 正式导入

# 7. 启动应用
docker compose --profile app up -d --build
# 或本地启动：
python -m uvicorn app.main:app --host 0.0.0.0 --port 9900

# 8. 验证
curl http://localhost:9900/api/v1/health/ready
```

### 8.3 常见问题

| 问题 | 解决方案 |
| --- | --- |
| Docker Hub 网络不通 | 配置镜像加速器或使用本地缓存的镜像 |
| Redis 端口冲突 | 修改 docker-compose.yml 和 .env 中的端口映射 |
| pip 编码错误 | `set PYTHONUTF8=1` 或升级 pip |
| Python 版本不匹配 | 项目要求 3.12，实测 3.11 可正常运行 |
| Milvus 连接失败 | 等待 docker compose 所有服务 healthy 后再操作 |
| open-webSearch 构建失败 | tag 本地 node 镜像为 node:20-alpine |

---

## 附录：项目文件导航

### 想了解某个功能，从这里开始读：

| 你想了解... | 先读这个文件 |
| --- | --- |
| 整体架构 | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| 应用入口 | [app/main.py](app/main.py) |
| 配置体系 | [app/config.py](app/config.py) |
| Skill 系统 | [app/skills/README.md](app/skills/README.md) → [app/skills/registry.py](app/skills/registry.py) |
| Fast 诊断图 | [app/agents/graph.py](app/agents/graph.py) → [app/agents/state.py](app/agents/state.py) |
| Deep 诊断图 | [app/diagnosis_graphs/deep_diagnosis_graph.py](app/diagnosis_graphs/deep_diagnosis_graph.py) |
| 权限系统 | [app/runtime/permissions.py](app/runtime/permissions.py) |
| 工具执行 | [app/runtime/tool_runner.py](app/runtime/tool_runner.py) |
| RAG 检索 | [app/rag/retrieval.py](app/rag/retrieval.py) → [app/core/hybrid_retriever.py](app/core/hybrid_retriever.py) |
| 队列系统 | [app/queue/redis_streams.py](app/queue/redis_streams.py) → [app/diagnosis_worker.py](app/diagnosis_worker.py) |
| 数据库 | [app/db/postgres.py](app/db/postgres.py) |
| MCP 工具 | [mcp_servers/system_server.py](mcp_servers/system_server.py) → [app/core/mcp_client.py](app/core/mcp_client.py) |
| 评测体系 | [benchmark/README.md](benchmark/README.md) → [benchmark/run_benchmark.py](benchmark/run_benchmark.py) |
| 前端 | [frontend/index.html](frontend/index.html) → [frontend/app.js](frontend/app.js) |

---

> **最后的话**：这个项目是作者的学习成果，体现了一个 Agent 开发者从"能跑"到"工程化"的成长路径。它的价值不在于代码有多完美，而在于它展示了真实世界中 Agent 系统需要考虑的工程问题——队列削峰、并发控制、权限边界、审计追溯、经验沉淀。理解这些并能在面试中有条理地讲述，比任何算法题都更能证明你的 Agent 开发能力。祝你求职顺利！🎉
