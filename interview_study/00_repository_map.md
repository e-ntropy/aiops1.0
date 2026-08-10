# 第一阶段仓库地图

> 审计基线：2026-08-04 当前工作区。本文只描述实际可见源码与已保存报告，不把 README、类名或注释单独当作运行证据。

## 1. 仓库定位

这是一个面向 OnCall/SRE 的 AIOps 参考实现：FastAPI 接收人工诊断或 Alertmanager 告警，统一进入 Fast/Deep LangGraph；同步入口以 SSE 返回，异步入口以 PostgreSQL 保存事实、Redis Streams 排队、Worker 执行并审计。知识检索使用 Parent-Child 分块、Milvus、进程内 BM25、加权 RRF 和可选 Reranker。

仓库指南明确称其为 **V3 参考实现而非生产就绪系统**（`AGENTS.md` §2）。面试中应说“可运行的个人公开参考实现”，不说“生产级平台”。

## 2. 面试相关目录

| 路径 | 类型 | 真实职责 | 关键入口/证据 |
| --- | --- | --- | --- |
| `app/api/v1/` | 生产代码 | HTTP/SSE 接入、Webhook、任务/队列/健康接口 | `aiops.py::aiops_diagnose()` L133；`submit_diagnose()` L38；`webhook.py::alertmanager_webhook()` L109 |
| `app/services/` | 生产代码 | 用例层；同步诊断并发准入、RAG Chat | `aiops_service.py::stream_diagnose()` L23；`rag_service.py::stream_chat()` L68 |
| `app/orchestration/` | 生产代码 | Fast/Deep 选择、统一事件流、Worker 审计 | `diagnosis_runner.py::run_diagnosis_graph()` L100；`audit.py::run_legacy_langgraph_with_audit()` L47 |
| `app/agents/` | 生产代码 | Fast 节点/State、Deep 四类专业 Agent | `graph.py::build_aiops_graph()` L95；`state.py::PlanExecuteState` L34；`state_deep.py::DeepDiagnosisState` L25 |
| `app/diagnosis_graphs/` | 生产代码 | 独立 Deep StateGraph、fan-out/fan-in、RCA 和报告 | `deep_diagnosis_graph.py::build_deep_graph()` L894 |
| `app/runtime/` | 生产代码 | Harness、预算/重复检测、权限、审批、工具执行 | `agent_harness.py::evaluate_replanner_pre_llm()` L708；`permissions.py::evaluate_permission()` L99；`tool_runner.py::run_parallel_agent()` L203 |
| `app/skills/` | 生产代码 + 配置内容 | `SKILL.md` 解析、注册、Router 卡片、Playbook/allowlist | `loader.py::load_skill_from_file()` L103；`registry.py::get_skill_registry()` L164；`definitions/*/SKILL.md` |
| `app/tools/`, `mcp_servers/` | 生产代码/进程边界 | 本地 Tool、MCP Tool 装载与五类 MCP Server | `mcp_loader.py::get_all_tools()`；`mcp_servers/*_server.py` |
| `app/rag/`, `app/core/` | 生产代码 | Parent-Child 上下文、向量库、BM25/RRF、Embedding/Rerank | `rag/retrieval.py::build_context()` L25；`core/vector_store.py::advanced_search()` L116；`core/hybrid_retriever.py::hybrid_search()` L241 |
| `app/queue/` | 生产代码 | Redis Streams 优先级流、Consumer Group、PEL 回收、ACK、DLQ | `redis_streams.py::RedisIncidentQueue` L44 |
| `app/incidents/`, `app/evidence/`, `app/db/` | 生产代码 | 事故、任务、Evidence、PostgreSQL schema | `incidents/repository.py::IncidentRepository` L144；`db/postgres.py::_SCHEMA_SQL` L85 |
| `benchmark/` | 实验/评测代码 | Router、Retrieval、RAGAS/OpenEvals、Diagnosis E2E | `run_skill_router_benchmark.py`；`run_benchmark.py`；`run_diagnosis_benchmark.py` |
| `benchmark/reports/` | 历史实验产物 | 指标结果与 checkpoint，不代表现环境必然复现 | `FINAL_METRICS_REPORT.md`；日期化 JSON |
| `benchmark/test_*.py` | 自动化评分测试 | 仅验证部分判分函数，不是业务 E2E 测试 | 两个 `unittest` 文件 |
| `data/kb_corpus/` | 版本化数据 | 公开知识库语料，当前约 955 个文件 | `scripts/ingest_kb_corpus.py` 消费 |
| `data/wiki/` | 运行时数据 | 经验存储；除约定外应忽略，可能含事故信息 | `app/wiki/store.py` |
| `frontend/` | 生产静态资源 | FastAPI 服务的 Web UI | `app/main.py` 静态挂载 |
| `docs/` | 说明/历史证据 | 架构、并发测试方法、历史压力结果 | 不能替代调用链证据 |
| `open-webSearch-main/` | 第三方 vendored | 独立 Node 搜索服务 | 与 Python 主链隔离，Apache-2.0 |

## 3. 代码、实验、测试与配置的边界

- **生产代码**：`app/`、`mcp_servers/`、`frontend/`。是否进入主链必须从 API/Worker 继续追到调用点。
- **实验代码**：`benchmark/run_*.py`、`scripts/ingest_kb_corpus.py`、`scripts/loadtest.py`。它们可能调用外部模型、写 Milvus/Postgres 或产生成本。
- **自动化测试**：仅定位到 `benchmark/test_skill_router_scoring.py` 和 `benchmark/test_diagnosis_benchmark.py`；没有提交的 `tests/` 业务套件或 CI。不能声称完整单元/集成/E2E 覆盖。
- **配置**：`app/config.py` 是运行时默认值与校验的代码事实；`.env.example` 是部署示例；本地 `.env` 含敏感配置，不作为文档证据且不得提交。
- **历史结果**：`benchmark/reports/*.json` 能证明某次特定环境运行，不保证当前代码、模型、索引和数据下重复得到相同数字。

## 4. 核心入口

### 在线入口

- App：`app/main.py::app` L86，路由注册 L155-L165。
- 同步 SSE：`POST /api/v1/aiops/diagnose` → `aiops.py::aiops_diagnose()` L133。
- 异步人工提交：`POST /api/v1/aiops/diagnose/submit` → `submit_diagnose()` L38。
- Alertmanager：`POST /api/v1/webhook/alertmanager` → `alertmanager_webhook()` L109。
- RAG Chat：`app/api/v1/chat.py::chat_stream()` L68。

### 后台入口

- `python -m app.diagnosis_worker --name <worker>` → `app/diagnosis_worker.py::main()` L265 → `DiagnosisWorker.start()` L46。

### 图入口

- Fast：`app/agents/graph.py::build_aiops_graph()` L95。
- Deep：`app/diagnosis_graphs/deep_diagnosis_graph.py::build_deep_graph()` L894。
- 统一选择与流式执行：`app/orchestration/diagnosis_runner.py::run_diagnosis_graph()` L100。

### 数据/评测入口

- 入库：`python scripts/ingest_kb_corpus.py --dry-run`；实际重建 `--reset --batch 8` 会删除 Collection 后重新写入，不能当静态检查随意运行。
- Retrieval：`python benchmark/run_benchmark.py retrieval --k 3`。
- RAGAS/OpenEvals：`python benchmark/run_benchmark.py ragas --limit 5`，会调用外部/付费模型。
- Router：`python benchmark/run_skill_router_benchmark.py`。
- E2E：`python benchmark/run_diagnosis_benchmark.py --modes fast deep`（具体参数见 `benchmark/README.md`）。

## 5. 启动方式

| 目标 | 命令 | 边界 |
| --- | --- | --- |
| 基础设施 | `docker compose up -d` | Milvus/etcd/MinIO/Redis/Postgres/open-webSearch 等 |
| 完整容器栈 | `docker compose --profile app up -d --build` | API、3 Worker、MCP 与基础设施 |
| macOS/Linux 本地进程 | `bash scripts/run_all.sh` | 基础设施容器 + 本地 Python 进程 |
| Windows 兼容启动 | `.\run.ps1` | 不是完整 V3 Postgres/Worker 拓扑 |
| API 就绪检查 | `curl -fsS http://localhost:9900/api/v1/health/ready` | 需要服务已启动 |

## 6. 初扫发现与环境限制

1. 工作区中可见 `.env`，本审计未读取/复制其内容。
2. 当前执行环境中 `git status` 返回 `fatal: not a git repository`，所以无法以 Git 基线确认既有改动；交付仅新增 `interview_study/` 文档。
3. `__pycache__` 与历史报告大量存在；它们不是源码证据。
4. 根目录已有 `RESUME_ANALYSIS.md` 和 `LEARNING_GUIDE.md`，但本阶段重新沿源码验证，不把其陈述直接复用为结论。

