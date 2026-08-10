# 指标复现与可信度审计

## 1. 结论先行

仓库保存了可复核的历史数据集、runner 与 JSON 报告，但多数结果是特定环境下的单次离线实验，不等于当前机器已经重新跑通，也不等于具有统计显著性。面试推荐用“历史报告显示/该次实验观察到”，避免使用“线上稳定达到”。

## 2. 核心数字逐项还原

| 简历数字 | 原始分母/公式 | 证据入口 | 可安全陈述 | 不能外推 |
| --- | --- | --- | --- | --- |
| Router 75% | 30/40 | `benchmark/skill_router_eval.jsonl`、router runner/report | 40 条离线集总体 75% | 生产流量准确率 |
| Skill 71.4% | 25/35 | 同上 | 有目标 Skill 的样本命中 25 条 | 所有 Skill 均衡 |
| OOS 100% | 5/5 | 同上 | 该 5 条均拒识正确 | 泛化 OOS 100% |
| Vector Hit@3 .800 | 40/50 | `retrieval_20260728-132308.json` | 该基线前三命中 40 条 | Parent-Child 的独立贡献 |
| Hybrid Hit@3 .860 | 43/50 | `retrieval_20260728-132213.json` | Hybrid 配置多命中 3 条、+6pp | 已统计显著 |
| Vector MRR@3 .710 | 50 条首命中倒数排名均值 | `132308` | 该基线排序质量 | 端到端准确率 |
| Hybrid MRR@3 .7767 | 同公式 | `132213` | 该次实验 +0.0667 | reranker 的独立收益 |
| Hybrid 无 rerank 同指标 | Hit .86、MRR .7767 | `retrieval_20260728-132714.json` | 保存报告未显示 rerank 增益 | rerank 永远无用 |
| RAGAS Faithfulness .8689 | 50 条均值 | `ragas_20260728-165320.json` | 单 judge 历史代理指标 | 人工事实正确率 |
| Answer relevancy .8849 | 50 条均值 | 同上 | 单次历史结果 | 当前 provider 必然复现 |
| Context precision .8833 | 50 条均值 | 同上 | 该上下文评测结果 | 所有真实查询质量 |
| Context recall .8817 | 50 条均值 | 同上 | 该 50 条均值 | corpus 完整性证明 |
| E2E 10×2 成功 | Fast 10、Deep 10 均完成 | diagnosis benchmark 历史报告 | 两模式各 10 次流程跑通 | 诊断都正确 |
| Fast RCA Top-1 .5 | 约 5/10 | E2E 历史报告 | 合成集该次命中 | 生产 RCA 准确率 |
| Deep RCA Top-1 .0 | 0/10 | E2E 历史报告 | 该次 gold 下未命中 | Deep 架构必然无效 |
| 四层 160 | 40+50+50+20 | 三个数据集 + 两模式运行 | 共 160 个 case/运行单元 | 160 条 E2E 样本 |

## 3. 安全复现顺序

以下只描述命令，不代表本次已运行外部模型评测。先确认 `.env`、服务、费用与数据范围。

```powershell
# 只读检查数据量
(Get-Content benchmark\skill_router_eval.jsonl).Count
(Get-Content benchmark\retrieval_rk_50.jsonl).Count
(Get-Content benchmark\ragas_qa_50.jsonl).Count

# 基础静态检查
.\.venv\Scripts\ruff.exe check app mcp_servers benchmark scripts
.\.venv\Scripts\python.exe -m compileall -q app mcp_servers benchmark scripts
docker compose config --quiet

# 可能调用外部 provider；确认凭据、成本、版本后再运行
.\.venv\Scripts\python.exe benchmark\run_benchmark.py retrieval --k 3
.\.venv\Scripts\python.exe benchmark\run_benchmark.py ragas --limit 5
```

## 4. 十五项复现审计清单

1. **Source**：每个数字必须指向具体 dataset、runner 和 report，不能只引用 README。
2. **Command**：保存完整命令、工作目录、退出码和开始/结束时间。
3. **Dataset count**：记录实际解析成功数、跳过数、重复数，不只记录文件行数。
4. **Formula**：固定 Hit/MRR/Recall、Top-1、success 的分母、K 值与匹配规则。
5. **Aggregation**：说明 macro/micro、按 query 还是按 group、失败样本是否计零。
6. **Model**：锁定生成、judge、embedding、reranker 的 provider、model/version 和维度。
7. **Randomness**：记录 temperature、seed（若支持）、并发顺序；重复运行报告方差。
8. **Repeat**：至少多次跑生成类评测；检索确定性也要检查索引和近似搜索波动。
9. **Cache**：分别报告冷/热缓存，记录 cache key、TTL 和是否复用了旧模型输出。
10. **Leakage**：冻结 test，检查 query/answer 与 corpus、dev、其他层数据的精确/语义重复。
11. **Failure**：超时、provider 错误、空结果必须计数；不得只对成功样本求均值。
12. **Selection**：说明场景覆盖、难度、来源与人工/合成比例，避免挑容易样本。
13. **Confidence interval**：小样本报告 bootstrap CI；配对二分类变化可用 McNemar。
14. **Latency/cost**：同时记录 P50/P95、token、provider 调用数，避免只优化质量。
15. **Interview phrasing**：使用“50 条离线集单次观察”“+6 个百分点”“未做显著性证明”等限定语。

## 5. 建议补做的最小实验

- Retrieval：固定 50 条，导出 Vector 与 Hybrid 逐题结果，统计 win/loss/tie，做配对 bootstrap/McNemar。
- Ablation：Dense 固定块 → Parent-Child Dense → Hybrid → Hybrid+Rerank，一次只改一个变量。
- Router：增加每 Skill 混淆矩阵、top-k、置信度与至少 30 条困难 OOS。
- RAGAS：多 judge 或人工抽检 15 条，记录一致率；失败计零与成功样本均值同时报告。
- E2E：扩充真实脱敏事故，分别报告“运行成功”“证据充分”“RCA Top-k”“处置安全”。

