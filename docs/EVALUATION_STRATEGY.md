# AIOps 评测策略

本文定义统一工作流、知识检索、诊断和事故闭环的评测边界。评测的目标不是用一个总分证明
“智能体足够聪明”，而是把失败定位到 Query、Scope、检索、证据、根因、权限或生命周期门禁。

## 1. 分层评测

| 层级 | 评测对象 | 当前数据/入口 | 主要指标 | 难度 |
| --- | --- | --- | --- | --- |
| L0 | Query、Capability、Scope、安全与生命周期契约 | `workflow_contract_eval.jsonl`、`lifecycle_contract_eval.jsonl` | Accuracy、Safety Pass、Exact Match | 低；纯离线、确定性 |
| L0.5 | 自适应诊断流程与 Evidence 隔离 | `diagnosis_fixture_eval.jsonl` | Phase/Mode、正反例行为、Evidence Recall、Isolation、Exact Match | 低；纯离线、确定性 |
| L0.6 | Memory 治理与 Tool 安全 | `memory_governance_eval.jsonl`、`tool_safety_eval.jsonl` | Precision/Recall、污染率、晋升、Fallback/Envelope Exact Match | 低；纯离线、确定性 |
| L1 | 知识检索 | `retrieval_rk_50.jsonl` | Hit@K、MRR@K、Recall@K | 中；依赖语料、Embedding、Milvus、Rerank |
| L2 | RAG 回答 | `ragas_qa_50.jsonl` | Faithfulness、Relevancy、Context Precision/Recall | 中高；生成与 Judge 都有随机性和费用 |
| L3 | Fast/Deep 诊断 | `diagnosis_e2e_10.jsonl` | 根因命中、证据覆盖、引用正确、延迟、Token | 高；依赖模型、工具可用性和图状态 |
| L4 | 真实事故结果 | 脱敏关闭事故的候选样本 | 人工根因接受率、恢复验证率、误处置率、MTTR | 最高；Gold 稀缺、存在反事实和安全风险 |

L0 和 L0.5 是每次提交都应运行的门禁；L1-L3 需要固定环境、版本和 Provider 后比较；L4 只能在人工审核、
脱敏和权限合规的真实运行中积累，不能用合成文本冒充。

## 2. 离线 Workflow Contract Benchmark

```bash
python benchmark/run_benchmark.py workflow
python benchmark/run_benchmark.py workflow --suite query
python benchmark/run_benchmark.py workflow --suite lifecycle
python benchmark/run_benchmark.py workflow --ids wf-knowledge-oom-conflict,lc-redaction-denied
python benchmark/run_benchmark.py workflow --enforce
```

该模式明确禁用 LLM、Milvus、Postgres、Redis 和真实系统采集，使用合成结构化快照验证：

- Intent、Capability、Phase、Scope、确认状态和风险等级是否命中 Gold；
- Query 拆分是否覆盖人工定义的任务目标组；
- 所有允许工具是否仍为只读；
- Scope 未解析时是否禁止进入 `ready`；
- 高风险请求是否必须进入确认；
- 本机/远程现场能力是否被普通后台 Worker 关闭式拒绝；
- 根因、计划、恢复、关闭和脱敏门禁是否按顺序生效；
- 恢复 Evidence 是否绑定同一 Scope 和真实 ToolCall ID；
- 只有完整闭环才允许 `verified_knowledge` 晋升。

当前数据集包含 240 条 Query 和 120 条生命周期用例，覆盖 12 个 Query Family 与 9 个事故生命周期
Family，并加入同义改写、多意图、否定表达、越权请求、数据源失败和脱敏拒绝。它适合回归契约，
但仍不足以估计生产分布上的语义泛化；错别字、中英混合、超长 Query 和真实组织表达仍需扩充。

版本化参考基线位于 `benchmark/baselines/workflow_contract_v1.json`。`--enforce` 同时验证数据集身份、
样本下限和质量阈值，并以非零状态报告回归。修改数据集必须更新版本化基线并接受审阅；否则
新增简单样本可能稀释失败率，删除困难样本也可能制造虚假提升。

## 2.1 离线 Diagnosis Fixture Benchmark

```bash
python benchmark/run_benchmark.py fixture
python benchmark/run_benchmark.py fixture --enforce
```

120 条自生成夹具显式标注 `task_class`、`polarity`、`difficulty`，覆盖正常/边界、正例/反例/未知、
简单/复杂，以及 Fast、Deep 和失败路径。每个 Observed Evidence 必须来自该 fixture，绑定受信
ToolCall ID 与当前 Scope；Runner 不允许访问真实机器。该层用于发现编排和证据污染回归，不评估
真实模型的语义推理能力，也不能替代真实事故人工 Gold。

## 2.2 Memory 与工具安全消融

240 条 Memory Gold 用同一组记录和期望集合比较 no-memory、flat-memory 与 governed-memory，避免把
不同数据集上的分数错误拼成“前后提升”。报告记录级判定、Precision/Recall、污染/泄漏、晋升准确率，
并以固定种子 paired bootstrap 给出增益区间。120 条 Tool Safety Gold 独立验证重试上限、替代数据源、
证据缺口、fail-closed，以及 Scope/Risk/Permission/Idempotency/Timeout 结构化信封。

这两套数据均为自生成离线契约。平铺 Memory 是消融对照，不代表项目曾在线上以该策略运行；0% 污染
表示该固定集合中未发生，不等同于未知生产分布中的绝对安全。

## 3. 事故关闭到 Benchmark 的治理闭环

```text
Closed Incident
    -> 人工确认根因与恢复
    -> 自动字段脱敏 + 人工复核
    -> Candidate Eval Sample（quarantine）
    -> 去重 / 相似事故聚类 / 泄漏检查
    -> 双人 Gold 审核
    -> 固定 train/dev/test 分组
    -> 版本化 JSONL
    -> 回归失败进入 Query / RAG / Tool / RCA / Policy 缺陷队列
```

关闭事故产生的样本只能进入候选区，不能自动追加到正式测试集。原因是：同一事故可能被 Wiki 或
Prompt 召回，直接加入测试集会产生数据泄漏；人工确认也可能有偏差；原始 Evidence 可能含主机名、
IP、账号、工单和内部端点。正式 Gold 至少需要：

- `redaction_passed=true`，且通过自动敏感模式扫描；
- 根因、关键 Evidence、预期 Capability/Scope 和关闭结论经过人工复核；
- 按 `incident_family_id` 分组切分，禁止同一事故家族跨训练集和测试集；
- 保存 `dataset_version`、Schema 版本、来源、审核人、时间和废弃原因；
- 发现 Gold 错误时修订数据集并保留变更记录，而不是修改评分器迁就输出。

## 4. 最难评的部分

### 根因正确性

真实事故常有多个共同原因，“唯一根因字符串相等”过于简单。应使用因果机制组、关键 Evidence
覆盖和人工接受结论组合判分。LLM Judge 只能作为辅助，不能同时担任被评模型和唯一裁判。

### 工具和现场证据

工具失败可能来自网络、权限、目标主机、数据源或系统本身。评测必须固定目标和时间窗，并把
“数据源不可用但正确降级”与“Agent 失败”区分开。模型文字不是工具成功证据。

### 恢复和优化效果

一次健康快照不能证明长期恢复，指标改善也可能来自流量自然下降。生产级评测需要时间序列、
变更前后对照、观察窗口和回滚记录。当前项目只验证同 Scope 新快照，不宣称因果效果。

### 在线业务价值

MTTR、人工操作次数、建议采纳率容易受事故严重度和团队经验影响。需要按事故类型和严重度分层，
同时监控误报、漏报、越权尝试和错误关闭率，不能只优化平均耗时。

## 5. 发布门禁建议

- L0 安全通过率必须为 100%，任何下降直接阻断发布。
- L0.5 Evidence 隔离通过率必须为 100%，任何真实宿主机来源混入夹具都阻断发布。
- L0.6 Candidate、跨 Session、过期/替代 Memory 污染率必须为 0，Tool Safety Exact Match 必须为 100%。
- L0 核心 Intent/Scope/Lifecycle 指标不得低于已批准基线。
- L1-L3 必须记录数据集哈希、模型、Embedding、Rerank、Prompt/Skill 版本和运行环境；配置不一致的
  报告不能直接做 A/B 结论。
- 有随机性的评测至少重复三次并报告均值、标准差和失败数；小数据集只报告事实，不给生产保证。
- 新功能必须同时增加成功、歧义、越权、数据源失败和降级用例。
- 外部 Provider 评测应先确认凭据、费用、数据范围和服务就绪，不作为普通静态检查自动运行。
