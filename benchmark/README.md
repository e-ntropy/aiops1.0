# Benchmark

这个目录放七套评测集和五个评测脚本:

- `ragas_qa_50.jsonl`: 50 条端到端 RAGAS QA, 每个场景 5 条。
- `retrieval_rk_50.jsonl`: 50 条检索侧 R@K 题, 每个场景 5 条。
- `skill_router_eval.jsonl`: 40 条 Skill Router/OOS 评测。
- `diagnosis_e2e_10.jsonl`: 10 条 Fast/Deep 诊断评测。
- `workflow_contract_eval.jsonl`: 32 条 Query/Capability/Scope/安全与对抗契约评测。
- `lifecycle_contract_eval.jsonl`: 9 条确认、恢复、关闭和 Memory 门禁评测。
- `diagnosis_fixture_eval.jsonl`: 16 条完全离线事故夹具，覆盖正常/边界、正例/反例/未知、简单/复杂。
- `run_benchmark.py`: 支持 retrieval / ragas / workflow / fixture 四种模式。
- `run_skill_router_benchmark.py`: Skill 选择与结构化 OOS 判分。
- `run_diagnosis_benchmark.py`: 根因、证据引用、延迟和 Token 评测。
- `run_workflow_benchmark.py`: 纯离线统一工作流与事故生命周期评测。
- `run_diagnosis_fixture_benchmark.py`: 纯离线 Fast → Evidence Gate → Deep 与证据隔离评测。

完整的分层难度、事故样本治理和发布门禁见
[AIOps 评测策略](../docs/EVALUATION_STRATEGY.md)。

## 纯离线工作流契约

这套评测不需要任何基础设施或 Provider，适合作为每次重构的第一道回归门：

```bash
python benchmark/run_benchmark.py workflow
python benchmark/run_benchmark.py workflow --suite query
python benchmark/run_benchmark.py workflow --suite lifecycle
python benchmark/run_benchmark.py workflow --enforce
```

报告写入 `benchmark/reports/workflow_contract_*.json`，Web UI 的“AIOps 质量评估”面板可查看
汇总和失败样本。它验证 Intent、Capability、Scope、二次确认、只读 Tool 白名单、后台执行亲和性、
生命周期阶段、关闭门禁和 Memory 晋升。当前规模仅用于契约回归，不代表生产准确率。

`--enforce` 使用 `benchmark/baselines/workflow_contract_v1.json`，同时校验数据集 SHA-256、
最小样本数和版本化参考阈值。数据集或阈值只能在审阅新失败、Gold 和安全影响后更新，不能为了让
门禁通过而静默降低标准。

## 纯离线事故夹具诊断

```bash
python benchmark/run_benchmark.py fixture
python benchmark/run_benchmark.py fixture --ids fx-host-disk-simple,fx-mysql-lock-complex
python benchmark/run_benchmark.py fixture --enforce
```

该 Runner 不调用真实 Fast/Deep LangGraph，也不访问 LLM、Milvus、Postgres、Redis、网络、Docker 或
宿主机采集器。它回放版本化结构化 Evidence，验证：

- Fast 直接完成、Evidence Gate 升级 Deep 和 Deep 失败关闭；
- 正例根因机制、反例不误报、未知/冲突样本保留不确定性；
- 指标、日志、配置、事件等预期证据类型覆盖；
- 每条 Observed Evidence 必须绑定 fixture ID、ToolCall ID 和当前 Scope；
- 禁止 `get_local_*`、本机健康快照等真实宿主机来源混入夹具。

`benchmark/baselines/diagnosis_fixture_v1.json` 固定数据集 SHA-256 和门槛。当前 16 条只能证明流程与
隔离契约，不代表 LLM 在真实事故上的根因准确率；后者仍由有成本、需固定环境的 E2E 评测负责。

## 前置条件

先确保 Docker 里的 Milvus 已启动, 并且已用当前 embedding 配置重建知识库:

```bash
docker compose up -d
python scripts/ingest_kb_corpus.py --reset --batch 8
```

## 检索侧 R@K

推荐先跑这个, 它最快, 能实时观察检索参数变化:

```bash
python benchmark/run_benchmark.py retrieval --k 3
```

常用参数:

```bash
# 看 R@5
python benchmark/run_benchmark.py retrieval --k 5

# 只跑某个场景
python benchmark/run_benchmark.py retrieval --scenario Kafka --k 3

# 关闭 rerank 做 A/B
python benchmark/run_benchmark.py retrieval --k 3 --no-rerank

# 关闭 hybrid 做 A/B
python benchmark/run_benchmark.py retrieval --k 3 --no-hybrid
```

输出指标:

- `hit@k`: top-k 中是否命中任意 gold。
- `mrr@k`: 第一个命中位置的倒数。
- `recall@k`: top-k 覆盖的知识点组比例。

Gold 规则:

- 旧格式 `relevant: [A, B, C]` 表示 A/B/C 是同一知识点的替代来源，命中任意一个即可。
- 多个独立知识点使用 `relevant_groups: [[A, B], [C, D]]`，组内是 OR，组间按覆盖率计算 recall。
- 这样 awesome 告警、自建 runbook、SOP 是替代答案时，不会因为未同时进入 top-k 而错误扣分。

## RAGAS 端到端

这个会调用 LLM 生成答案, 再用 RAGAS judge 打分, 会比较慢:

```bash
python benchmark/run_benchmark.py ragas --limit 5
```

默认同时运行 OpenEvals:

- `groundedness`: 回答是否由检索上下文支持。
- `helpfulness`: 回答是否真正解决用户问题。

如只想运行原来的 RAGAS 四项:

```bash
python benchmark/run_benchmark.py ragas --limit 5 --no-openevals
```

加 `--verbose` 会打印 OpenEvals 的扣分原因，并写入 JSON 报告。

全量 50 条建议启用逐条 checkpoint:

```bash
python benchmark/run_benchmark.py ragas \
  --checkpoint benchmark/reports/ragas_full_checkpoint.json

# 中断后只重试未完成或失败的条目
python benchmark/run_benchmark.py ragas \
  --checkpoint benchmark/reports/ragas_full_checkpoint.json \
  --resume
```

结果会逐题打印滚动均值, 并写入:

- `benchmark/reports/retrieval_*.json`
- `benchmark/reports/ragas_*.json`

当前依赖组合下，本地 `BAAI/bge-reranker-v2-m3` 会因 tokenizer
兼容性错误降级。需要可比的全量结果时使用 `--no-rerank`，不要把静默降级结果
标成“已启用 rerank”。

## Skill Router 与 OOS

```bash
python benchmark/run_skill_router_benchmark.py
```

OOS 只有在 Router 返回结构化 `ROUTER_OUT_OF_SCOPE` transition reason
时才判为 OOS；普通非空回复不再算 OOS 命中。LLM 请求异常计入总分母并单独统计，
避免网络失败被误报成路由能力。

## Fast/Deep 端到端诊断

该评测直接调用两套 LangGraph，强制 `read_only`，不提交队列任务、不写
Postgres，也不执行处置:

```bash
python benchmark/run_diagnosis_benchmark.py \
  --modes fast,deep \
  --no-rerank \
  --checkpoint benchmark/reports/diagnosis_full_checkpoint.json
```

输出指标:

- `root_cause_top1_accuracy`: 根因章节是否覆盖所有预定义因果机制组。
- `mean_evidence_group_coverage`: 收集证据对 gold evidence groups 的覆盖率。
- `mean_citation_validity`: 引用 ID 是否可解析到真实 Evidence。
- `mean_citation_correctness`: 引用有效率 × 被引用证据的 gold 覆盖率。
- `latency_p50_sec` / `latency_p95_sec`: 每种模式端到端延迟。
- `mean_tokens`: Provider 返回的真实输入/输出 Token 合计。

Fast state 目前没有稳定 Evidence ID，因此 Fast 的 citation 指标固定为 0。
这代表产品能力缺口，不应通过文本中伪造 `ev_X` 绕过。

该入口会真实运行 Agent 与工具，若事故样本没有目标绑定，可能读取执行 Runner 的宿主机状态。
因此它不能替代 `fixture --enforce` 的隔离门禁；运行前必须确认目标、数据范围、Provider 费用和
采集权限，并在报告中记录运行环境。

若只修改了确定性判分规则，可复用已保存的模型输出，不产生新的 LLM 调用:

```bash
python benchmark/run_diagnosis_benchmark.py \
  --rescore-report benchmark/reports/diagnosis_e2e_<timestamp>.json
```
