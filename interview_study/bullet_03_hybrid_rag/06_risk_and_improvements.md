# Hybrid RAG：风险与改进

| 风险 | 当前实现 | 改进/验收 |
| --- | --- | --- |
| Parent内容冗余 | 每个child保存全文 | 父表/DocStore按ID查询；版本一致性测试 |
| BM25规模上限 | Milvus query limit=16384 | 分页或独立稀疏索引；全量数量核对 |
| 中文tokenizer较弱 | 字符/ASCII token策略 | jieba/领域词典/ES analyzer消融 |
| `_key`使用Python hash | 进程间不稳定 | SHA256稳定键 |
| Parent-Child无独立消融 | 只有Hybrid on/off | fixed chunk vs parent-child同配置A/B |
| Reranker无历史增益 | on/off指标相同 | 修复依赖后在冻结集复跑并报告P95 |
| 文档/索引版本漂移 | 无显式index version | corpus hash、embedding model、schema写metadata |
| 50条统计不稳 | 点估计 | bootstrap CI、按场景报告、McNemar/paired bootstrap |

