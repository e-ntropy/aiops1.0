# Multi-Agent AIOps Platform — 可复现评测报告

> 评测日期: 2026-07-28  
> 离线工作流契约追加日期: 2026-08-10
> 环境: Windows 11、Python 3.11、Docker Desktop、DeepSeek API  
> 本地模型: `BAAI/bge-small-zh-v1.5` embedding  
> 说明: 以下仅记录实际执行结果，不把 smoke test、静态检查或理论分析写成总体质量。

## 1. 结果总览

| 评测 | 样本/运行 | 核心结果 | 原始报告 |
| --- | ---: | --- | --- |
| Retrieval | 50 | Hybrid hit@3 0.860、MRR@3 0.777、recall@3 0.860 | `retrieval_20260728-132213.json` |
| Skill Router | 40 | 总体 75.0%、非 OOS 71.4%、OOS 100% | `skill_router_20260728-080603Z.json` |
| RAGAS + OpenEvals | 50/50 成功 | faithfulness 0.869、helpfulness 0.898 | `ragas_20260728-165320.json` |
| Diagnosis E2E | 10 × Fast/Deep，20/20 成功 | Fast Top-1 50%、Deep Top-1 0% | `diagnosis_e2e_rescored_20260728-091959Z.json` |
| Workflow Contract | Query 20 + Lifecycle 9 | Query Exact 100%、Safety 100%、Lifecycle Exact 100% | `python benchmark/run_benchmark.py workflow` |

## 2. Skill Router：修正后的 OOS 判分

| 指标 | 结果 |
| --- | ---: |
| 总体准确率 | 75.0% (30/40) |
| Skill 匹配准确率（排除 OOS） | 71.4% (25/35) |
| OOS 检测准确率 | 100% (5/5) |
| 含“兜底” transition reason 比例 | 12.5% |
| LLM 调用失败率 | 0% |
| 总耗时 | 39.8 秒 |

判分修正后，OOS 必须由结构化 `ROUTER_OUT_OF_SCOPE` transition reason
确认。普通非空 response 不再直接算 OOS；异常也不会从分母中消失。本次 40 条运行
无 LLM 请求失败，因此 OOS 5/5 是有效结果，但样本量仍小，不能外推为线上 100%。

## 3. 全量 50 条 RAGAS

本次使用全部 50 条、10 个场景，每个样本均完成 RAGAS 四项与 OpenEvals 两项，
无失败。使用逐条原子 checkpoint；本地 reranker 因 tokenizer 依赖不兼容而关闭。

| 指标 | 50 条均值 |
| --- | ---: |
| faithfulness | 0.869 |
| answer relevancy | 0.885 |
| context precision | 0.883 |
| context recall | 0.882 |
| groundedness | 0.958 |
| helpfulness | 0.898 |

总耗时 2032.5 秒（约 33.9 分钟）。此前 5 条 Redis smoke test 的
faithfulness=1.0 不能代表总体质量；全量结果不支持“零幻觉”表述。

## 4. Fast/Deep 端到端诊断

### 4.1 数据与判分

10 条轻量合成事故覆盖 Redis、MySQL、Nginx、Kafka、Kubernetes、JVM、磁盘和
网络。每条分别运行 Fast 与 Deep，强制 `read_only`。根因 Top-1 按因果机制组
严格匹配；引用正确率等于引用 ID 有效率乘以被引用证据对 gold evidence groups
的覆盖率。

Fast 报告的根因章节提取已支持“二、根因分析”等带中文序号标题。最终结果是基于
保存的完整输出重新确定性判分，不产生额外模型调用。

### 4.2 结果

| 指标 | Fast | Deep |
| --- | ---: | ---: |
| 成功运行 | 10/10 | 10/10 |
| 根因 Top-1 准确率 | 50.0% | 0.0% |
| 平均证据组覆盖率 | 68.3% | 94.2% |
| 平均引用 ID 有效率 | 0.0% | 100.0% |
| 平均引用正确率 | 0.0% | 94.2% |
| 平均延迟 | 43.8 秒 | 14.2 秒 |
| P50 延迟 | 36.9 秒 | 12.4 秒 |
| P95 延迟 | 83.6 秒 | 32.9 秒 |
| 平均 Token | 35,991 | 11,983 |
| 总 Token | 359,914 | 119,831 |

整组耗时 580.5 秒，20/20 无运行错误。

### 4.3 关键发现

1. Deep 收集和引用了大量“真实本机”证据，但被本机 87%–90% 内存占用、
   `vmmemWSL` 和磁盘状态污染，把合成的 Redis/MySQL/Kafka/网络事故普遍归因到
   本机资源压力。引用形式正确，不代表根因正确。
2. Fast 在 5/10 样本命中严格根因，但也受同一污染影响；MySQL replication、
   Kafka lag、host disk 和 connection refused 等样本出现错误归因。
3. 当前 Fast 比 Deep 慢约 3.1 倍、平均 Token 约 3.0 倍。模式名称不应直接被解释为
   成本承诺，应先检查 Fast graph 的循环次数、工具重复调用和上下文累积。
4. Fast graph 没有稳定 Evidence ID，所以无法验证引用实体；citation 指标记 0 是
   产品缺口，而不是评测缺失。

## 5. 本机运行与真实数据源建议

实测硬件为 Ryzen 5 5600H、15.9GB RAM、RTX 3050 Laptop 4GB VRAM，而不是
8GB VRAM。当前同时运行 Milvus、Postgres、Redis、OpenSearch/Attu 与本地 embedding
时可用内存偏低。

| 方案 | 接入复杂度 | 本机可行性 | 建议 |
| --- | --- | --- | --- |
| 固定 fixture / replay MCP | 1–2 天 | 高 | 优先；让每条事故读取隔离的 metrics/logs，消除本机污染 |
| Prometheus + synthetic exporter | 1–2 天 | 高 | 保留 1–3 天、低采样频率，可做真实 PromQL 证据 |
| Loki + Promtail | 2–4 天 | 中 | 限制 retention；与 Milvus/本地模型同跑时内存较紧 |
| Elasticsearch/OpenSearch 日志栈 | 3–5 天 | 低 | 16GB RAM 下不建议作为本机 benchmark 基线 |
| 大模型本地推理 | — | 低 | 4GB VRAM 不适合当前 Agent 全链路；继续使用远端 chat + 本地小 embedding |

下一阶段最有价值的改动不是扩大 benchmark 数量，而是增加
`incident_id -> isolated evidence fixture` 绑定，让工具只能读取该事故的数据源；
之后再复跑相同 10×2，才能测出 Agent/RAG 本身的诊断提升。

## 6. 简历表述边界

可以写:

- 构建 40 条 Skill Router/OOS、50 条 RAGAS 和 10×2 Fast/Deep 端到端评测，
  支持逐条 checkpoint、失败隔离和无模型重判分。
- 在全量 50 条上得到 faithfulness 0.869、groundedness 0.958、
  helpfulness 0.898。
- 通过根因 Top-1、证据引用正确率、延迟与 Token 指标发现真实工具证据污染：
  Deep 引用正确率 94.2%，但严格根因 Top-1 为 0%，并据此设计事故级证据隔离。

暂时不要写:

- “零幻觉”或总体 faithfulness=1.0。
- “Deep 比 Fast 更准确”。
- “生产级 AIOps”或“真实生产事故准确率”。
- 本地 reranker 已有效带来增益。

## 7. 统一工作流离线契约基线（2026-08-10）

本次不调用 LLM、Milvus、Postgres、Redis 或真实系统工具。20 条 Query Gold 覆盖知识、状态、
巡检、诊断、优化、容量、复盘、评测、越界、写操作和意图冲突；9 条生命周期 Gold 覆盖人工
纠正、计划拒绝、恢复失败、缺基线、Scope 不一致、采集失败、脱敏拒绝和完整关闭。

| 指标 | 修复前 | 修复后 |
| --- | ---: | ---: |
| Query Intent Accuracy | 75.0% | 100.0% |
| Query Capability Accuracy | 75.0% | 100.0% |
| Query Scope Accuracy | 100.0% | 100.0% |
| Query Safety Pass | 100.0% | 100.0% |
| Query Exact Match | 75.0% | 100.0% |
| Lifecycle Stage / Closure / Memory / Exact | — | 100.0% |

失败分析推动了五项规则修复：解释型故障术语保持知识意图、评测语义优先于通用“运行”、非
AIOps 请求关闭式终止、纯写操作进入只读优化确认门、以及“当前……是什么意思”不自动升级为
现场查询。数据集 SHA-256：Query `b17ed3c9...df93`，Lifecycle `e3935b3e...e7fc`。

该结果仅证明 29 条确定性契约全部满足，不代表真实故障诊断准确率。面试或简历可以表述为
“构建离线安全回归并用失败样本驱动 Query 路由从 75% 提升到 100%（20 条合成契约）”，必须
同时保留样本规模和离线性质。
