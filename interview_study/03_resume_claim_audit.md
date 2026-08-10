# 四条简历真实性审计

## 0. 标记规则

- **已验证**：源码存在、已沿调用链确认进入主链，并与表述一致。
- **部分验证**：主干存在，但覆盖范围、语义或接线弱于表述。
- **与代码不一致**：源码行为与句子直接冲突。
- **仅测试/实验**：只在 benchmark 或辅助脚本中。
- **仅文档描述**：未找到可执行接线。
- **无法复现**：存在结果但本阶段未/不能安全重跑。
- **简历风险**：面试中按原句说容易被反例击穿。

## 1. 双模式 Agent 编排

原句：面向常规问答与复杂故障诊断，基于 LangGraph 构建 Fast/Deep 双状态图，分别执行路由、规划、动态重规划及日志、指标、基础设施、Runbook Agent 并行分析；通过步数预算、重复检测和异常降级保证任务有界执行。

| 原子声明 | 结论 | 源码证据 | 审计说明 |
| --- | --- | --- | --- |
| 使用 LangGraph | 已验证 | `agents/graph.py` L46；`deep_diagnosis_graph.py` L38 | 均导入并构建 `StateGraph` |
| Fast/Deep 是两个独立状态图 | 已验证 | `build_aiops_graph()` L95；`build_deep_graph()` L894 | 不是单图分支；runner 图外选择 |
| 两图进入主调用链 | 已验证 | `diagnosis_runner.py` L145-L166、L180-L183 | 同步与 Worker 统一复用 runner |
| Fast 做 Skill 路由/规划/执行/重规划 | 已验证 | Fast 构图 L104-L130 | 条件循环真实存在 |
| Deep 做四类专业 Agent | 已验证 | `SPECIALISTS` L60-L65；解析函数 L68-L89 | 四个均映射真实 `run_*_agent` |
| 四类 Agent 并行 | 已验证但需限定 | Deep 构图 L917-L920；State reducer L43-L46 | 图静态 fan-out；未派遣节点会 skip，不是每次四个都调用 LLM |
| Dynamic Replan | 部分验证 | `replanner.py` L382-L404；reroute L301-L332 | 只属于 Fast；Deep 无 Replan |
| 步数预算 | 部分验证 | config L369；Harness L708-L725；runner recursion limit | Fast 有；Deep 靠固定 DAG/agent max_iters。Token budget 主要事后告警 |
| 重复检测 | 已验证但较弱 | `agent_harness.py::_has_repeated_steps()` L803-L819 | 只检测最近 3 个规范化后完全相同 step，不是语义去重 |
| 异常降级 | 部分验证 | Router/Planner/Replanner fallback；四 Specialist except；RCA fallback | 节点级降级真实；Deep 图级异常不会自动降 Fast |
| 有界执行 | 部分验证 | Fast max step/recursion；Deep 固定 DAG/max_iters；Worker timeout | 同步 Deep 缺整图外层 timeout；远端调用仍依赖组件超时 |
| checkpoint/恢复执行 | 未验证/不存在主链实现 | 两图 `compile()` 无 checkpointer | Worker 重跑不等于图断点恢复 |

**简历风险**：原句容易让人理解成“两个图都包含 Router、Planner、Replan 和四专家”。真实结构是 Fast 负责 Skill/Plan/Execute/Replan，Deep 负责确定性多专家取证/RCA。建议后续改成：“构建独立 Fast Plan-Execute-Replan 图与 Deep Specialist fan-out/fan-in 图……”。

## 2. 渐进式 Skill 路由

原句：针对误路由和工具越权问题，设计“元数据路由—Playbook 按需加载—工具权限校验”机制，引入 OOS 识别、证据门槛与重路由上限，在 40 条 Router/OOS 测试集上取得 75.0% 准确率。

| 原子声明 | 结论 | 源码证据 | 审计说明 |
| --- | --- | --- | --- |
| Skill 元数据路由 | 已验证 | `Skill.to_router_card()` L102-L109；Router L125-L130 | Router menu 不含完整 playbook |
| Playbook 按需加载 | 已验证 | loader L115-L149；Planner L32-L54 | 所有 Skill 对象启动时加载进内存，但完整正文只在选定后注入 Planner；应称“prompt 按需披露”，不是磁盘懒加载 |
| 工具权限校验 | 已验证（Fast） | `filter_tools_for_skill()` L73-L169；`evaluate_permission()` L99-L196；tool runner L335-L418 | 模型前隐藏 deny，执行前再按工具名/decision 守门 |
| OOS 识别 | 已验证 | `SkillChoice.is_oncall`；Router L149-L164；fallback keyword | 结构化 transition 可判分 |
| 证据门槛 | 已验证但语义有限 | `replanner.py::_validate_reroute()` L180-L185；config L377-L382 | 门槛是 `len(past_steps)>=2`，不验证 Evidence 质量/来源 |
| 重路由上限 | 已验证 | config L370-L375；validator L187-L202 | 默认 1，并有当前/历史 Skill 防回环 |
| 路由历史 | 部分验证 | `tried_skills` reducer 与 `transition_history` | 保存被放弃 Skill/理由，不保存每次 Router 全候选分数 |
| 防工具越权覆盖 Deep | 与代码不一致 | Deep Agents `decisions=None`（如 metric L132-L140） | Deep 依靠硬编码工具集合/只读约束，未统一走 PermissionMode |
| 40 条数据集 | 已验证 | `benchmark/skill_router_eval.jsonl` 共 40 行 | 含 35 Skill + 5 OOS（历史报告） |
| 75.0% | 已验证为历史结果 | `skill_router_20260728-080603Z.json`: 30/40 | 运行脚本存在；本阶段未调用远端 LLM 复跑，属于特定 2026-07-28 环境结果 |

**简历风险**：不要说“Playbook 文件运行时才加载”，因为 Registry 在首次构建时扫描并解析全文；准确说法是“Router prompt 只披露 metadata，选中后 Planner prompt 才披露完整 Playbook”。不要暗示 Deep 已统一权限链。

## 3. Hybrid RAG 优化

原句：针对运维知识召回不足，实现 Parent-Child 分块、Milvus 子块召回及父文档回溯，结合 BM25、向量检索与 RRF 融合，使 Hit@3 从 0.800 提升至 0.860，MRR@3 从 0.710 提升至 0.777。

| 原子声明 | 结论 | 源码证据 | 审计说明 |
| --- | --- | --- | --- |
| Parent-Child 分块 | 已验证 | `splitter.py::split_markdown()` L116-L204 | child 保存稳定 parent hash 和 parent 文本 |
| Milvus 子块召回 | 已验证 | VectorStore text=`content`；`build_context()` L38-L44 | embedding/search 的 Document 是 child |
| 父文档回溯 | 已验证 | `retrieval.py` L48-L82 | 实际不是另查父表，而是从 child metadata 读冗余 `parent_content` |
| BM25 | 已验证 | `_BM25Index` L88-L166；Milvus 全量构建 L189-L238 | 进程内、惰性构建；最多查询 16384 chunks |
| 向量+RRF | 已验证 | `advanced_search()` L163-L188；`hybrid_search()` L277-L301 | 使用带权 RRF，不是纯经典等权 RRF |
| Reranker | 已实现但本指标无贡献证据 | reports 四组 A/B | 132213 标记 rerank=true，但 132714 rerank=false 得到完全同指标；RAGAS 全量实际关闭 rerank |
| Hit 0.800→0.860 | 已验证为保存 A/B | `retrieval_...132308` vs `...132213` | 同为 50 条、k=3、retrieve_k=30；差别 hybrid false/true |
| MRR 0.710→0.777 | 已验证为保存 A/B | 同上 | 0.776666... 四舍五入 0.777 |
| 改善完全由 Parent-Child 导致 | 未验证 | 两次报告都走当前 Parent-Child | 该 A/B 隔离的是 Hybrid 开关，不是 Parent-Child 消融 |
| 显著性 | 未验证 | 无置信区间/显著性检验 | 只有 50 条点估计；Hit 多 3 条 |

**简历风险**：把整个提升归因于“Parent-Child+Hybrid+RRF”过强。现有 A/B 能直接证明 Hybrid on/off 的提升，不能证明 Parent-Child 的独立贡献；Reranker 也未显示增益。

## 4. 可靠运行时与评测体系

原句：基于 PostgreSQL、Redis Streams 实现消息确认、Pending 回收、失败重试、DLQ 与并发控制，搭建 Router、Retrieval、RAGAS、E2E 四层评测体系；累计完成 160 次评测运行，在 50 条 QA 测试集上取得 Faithfulness 0.869、Context Recall 0.882、Groundedness 0.958、Helpfulness 0.898。

| 原子声明 | 结论 | 源码证据 | 审计说明 |
| --- | --- | --- | --- |
| PostgreSQL 事实存储 | 已验证 | `postgres.py` L85-L269；Repository 调用 | Alert/Task/Evidence/Run/Tool/Approval 等真实接线 |
| Redis Streams Consumer Group | 已验证 | `ensure_group()` L111-L126；`read_tasks()` L165-L213 | 优先级多 Stream + 同 group |
| ACK | 已验证 | queue L285-L291；worker success L144-L150 | 成功后 ACK；DLQ 也 ACK 原消息 |
| Pending 回收 | 已验证 | queue L225-L283；worker L54-L65 | 使用 `XAUTOCLAIM` |
| 失败重试 | 部分验证 | worker L161-L197 | 有最大次数与重新 XADD；没有退避、没有可重试/不可重试分类 |
| DLQ | 已验证 | queue L293-L333 | 保存原消息/原因；未定位到主链 DLQ replay 管理功能 |
| 并发控制 | 已验证 | `distributed_slot()` in service/worker | Redis 全局槽区分 manual/worker |
| 幂等 | 部分验证 | DB active-task unique index L174-L182；worker succeeded check L101-L106 | at-least-once 防重存在；跨 DB/Redis 操作非事务，不能称 exactly-once |
| 四层评测 | 已验证 | 三个 runner + 四份数据/结果 | E2E 是 10×2 合成事故，不是生产事故 |
| 160 次运行 | 已验证为一次汇总口径 | 40 Router + 50 Retrieval + 50 RAGAS + 20 E2E | “累计”易误解为 160 次完整系统运行；应说“累计执行 160 个评测样本/模式运行” |
| 50 条 QA | 已验证 | `ragas_qa_50.jsonl` 50 行 | 10 场景×5；Gold 构造方式仍需第二阶段逐条审计 |
| 四个 QA 指标 | 已验证为保存结果 | `ragas_20260728-165320.json` | 50/50 成功；DeepSeek chat judge、本地 bge-small embedding、reranker disabled |
| 可完全重复 | 无法保证 | 报告记录模型/配置与 checkpoint | LLM judge、远端模型、索引/语料有随机/版本依赖；未给随机种子/置信区间 |

**指标口径**：160 = 40 + 50 + 50 + (10×2)。这四组不是同质“E2E 运行”，其中 Retrieval 不生成答案，Router 只跑节点，RAGAS 每条又包含多个 judge。面试表述应主动解释口径。

## 5. 额外高风险发现

1. **Deep 事故上下文未贯通**：Worker audit 有 task/group IDs，但 `run_diagnosis_graph()` 签名和 `graph_input` 不传它们；Deep IncidentManager 在当前统一入口始终看不到 task_id。
2. **Deep Evidence 未逐条持久化**：事件转换产生 `type=evidence`，audit 没处理它；数据库里的报告 Evidence 不能替代四个原始 Specialist Evidence。
3. **Deep 权限不统一**：四 Agent 调 `run_parallel_agent(... decisions=None)`；Infra 是硬编码只读，Metric/Log/Runbook 也使用限定工具，但不是 PermissionMode/审批策略的统一执行证据。
4. **无 Graph checkpoint/HITL 主链**：存在 `hitl.py` 不等于已接线；不得在简历或面试中声称支持断点恢复/人工暂停继续。
5. **E2E 反证要主动讲**：历史报告显示 Fast Top-1 50%，Deep Top-1 0%，Deep Evidence 引用正确率高但受本机证据污染。不能说“Deep 更准确”；可以说评测发现证据隔离是下一步重点。
6. **运行时重试不分类**：`AgentHarness.classify_error()` 存在，但 Worker 未用；当前捕获的 Exception 统一按 attempts 重试。
7. **无 DLQ 重放闭环**：DLQ 写入和状态观察存在，未找到正式 replay API/CLI。
8. **测试覆盖有限**：两个 unittest 只验证评分逻辑；可靠运行时主要由手工/压力指南与历史结果支撑。

## 6. 第一阶段建议的安全面试版本

> 我把系统拆成两个独立 LangGraph：Fast 是 Skill Router、Planner、工具 Executor 和 Replanner 的有界循环；Deep 是规则 EvidencePlan 驱动的 Specialist fan-out/fan-in，四类 Agent 只回传结构化 Evidence，再做 Reducer、RCA 和报告。异步链路用 PostgreSQL 保存任务事实、Redis Streams 做 at-least-once 投递，并实现 ACK、XAUTOCLAIM、重试和 DLQ。检索用 child 召回、parent metadata 回溯与 BM25/向量加权 RRF。评测按 Router 40、Retrieval 50、RAGAS 50、Fast/Deep E2E 20 个样本/模式运行统计为 160；这些是特定实验环境结果，不代表生产准确率。当前明确缺口是 Deep 任务上下文/Evidence 持久化/统一权限尚未完全贯通，也没有 LangGraph checkpoint。

