# 五分钟项目介绍

## 1. 背景与目标（约 40 秒）

这个项目是一个面向 OnCall 和 SRE 的多 Agent AIOps 诊断工作台。传统告警平台能告诉你“哪里红了”，但根因分析通常需要跨日志、网络、容器、知识库和历史经验反复取证。我希望把这套过程做成可执行、可审计、可恢复的 Agent 流程，而不是只让一个大模型自由对话。

## 2. 诊断架构（约 80 秒）

入口接收用户报告或 Alertmanager 事件，Skill Router 根据结构化规则和 LLM 结果选择 playbook，并保留 OUT_OF_SCOPE 拒识。之后有 Fast 和 Deep 两种 LangGraph：Fast 是 `Router → Planner → Executor → Replanner → Report`，适合低复杂度、低延迟任务；Deep 会先生成 Evidence Plan，再让日志、网络、系统等 specialist 隔离执行，最终归并 Evidence、生成 RCA、处置建议和报告。

关键设计是 specialist 的私有对话不进入共享图状态，只输出压缩 Evidence；证据包含来源、摘要和审计关联。这样减少上下文膨胀，也让结论能回溯到工具调用。写操作仍经过 Skill、permission、guardrail 和 approval 边界，模型文本本身不能证明处置成功。

## 3. Hybrid RAG（约 70 秒）

运维文档同时包含自然语言、错误码、命令和连续操作步骤。固定小块容易命中但上下文不完整，大块完整但语义被稀释。因此摄取时按 Markdown 标题生成 Parent，再切 Child；Child metadata 保存 `parent_id` 和 `parent_content`。检索时用 Milvus dense 和进程内 BM25 取候选，通过 `weight/(rrf_k+rank+1)` 做 weighted RRF，最后按 Parent 去重并回填父内容。

历史 50 条报告里，向量基线 Hit@3/MRR@3 是 .800/.710，Hybrid 是 .860/.7767，即多命中 3 条。但两组都已有 Parent-Child，且另一份关闭 rerank 的 Hybrid 报告指标相同，所以准确说法是“Hybrid 配置观察到提升”，不能把全部收益归因于 Parent-Child 或 reranker。样本也没有显著性检验。

## 4. 异步可靠性（约 70 秒）

高并发诊断不在 HTTP 请求进程同步运行。Postgres 保存 alert、incident、task、agent run、tool call、evidence、approval 和 report 等事实；Redis Streams 负责 `XADD/XREADGROUP/XACK`、Consumer Group 和 PEL。Worker 启动时先用 `XAUTOCLAIM` 恢复 stale pending，再读取新消息。成功路径先提交 task 成功再 ACK，因此语义是 at-least-once；若 ACK 前崩溃，重新认领后会用 succeeded 状态短路。

这里我也会主动讲不足：Postgres 与 Redis 没有 Transactional Outbox，双写存在故障窗口；Worker 目前未把错误分类接入重试，也没有指数退避；DLQ 有写入但管理和 replay 闭环不完整；task 状态防重也不能替代外部工具的幂等。

## 5. 评测与结果（约 60 秒）

我把评测拆成四层：40 条 Router 测 Skill/OOS，50 条 Retrieval 测 Hit/MRR/Recall，50 条 RAGAS QA 测回答和上下文代理质量，Fast/Deep 各 10 次 E2E 测流程运行与 RCA。合计是 160 个 case/运行单元，不是 160 条完整 E2E。

历史 Router 总体 75%，其中 OOS 5/5、Skill 25/35；RAGAS 50 条报告 faithfulness 约 .869。E2E 两模式当时都 10/10 跑通，但 Fast RCA top-1 约 .5、Deep 为 .0，再次说明“流程成功”不等于“诊断正确”。下一步优先补真实脱敏事故、独立 test、配对置信区间、Outbox 和副作用幂等，然后再讨论扩大模型或 Agent 数量。

## 收尾句

这个项目让我真正掌握的不是某个框架 API，而是如何把 Agent 的非确定性约束在一套可追踪证据、权限边界、可恢复运行时和分层评测里，并且能诚实地区分已实现、历史实验与生产化目标。

