# 架构取舍速答

| 取舍 | 为什么当前这样做 | 代价/风险 | 何时换方案 |
| --- | --- | --- | --- |
| Fast vs Deep | Fast 低延迟通用闭环；Deep 专家隔离与证据归并 | Deep 成本、延迟、噪声更高 | 低复杂度默认 Fast；高影响且多域证据才 Deep |
| Rule Router vs LLM Router | 规则确定、低成本；LLM 覆盖自然语言 | 规则脆、LLM 随机且昂贵 | 规则处理高置信模式，LLM 处理剩余并保留 OOS |
| Single-agent vs Multi-agent | 单体简单；多 Agent 隔离上下文与专业工具 | 多 Agent 协调和证据冲突 | 问题确实跨域且并行收益高时使用多 Agent |
| Shared state vs Private specialist state | 共享压缩 Evidence，私有推理不外泄 | 压缩可能丢信息 | 共享可审计结论与引用，不共享完整对话 |
| Child retrieval vs Parent generation | 精准定位与完整回答兼顾 | 父内容冗余、token 增加 | 语料很短时可统一粒度；大规模拆父表 |
| Dense vs BM25 | Dense 擅长语义，BM25 擅长错误码/命令 | 两套索引和融合复杂度 | 查询单一且数据验证无互补时简化 |
| RRF vs score fusion | 无需校准异构分数，鲁棒易解释 | 丢失分数间隔 | 有稳定标注后尝试归一化或 learned fusion |
| Rerank vs no rerank | 可改善候选精排 | 额外延迟/成本；历史报告未证收益 | 难候选集有稳定 nDCG/MRR 增益再启用 |
| Milvus vs pgvector | 独立向量服务与 ANN 扩展 | 多一套基础设施 | 数据小、已重度依赖 PG 时优先 pgvector |
| Milvus vs Elasticsearch | 向量检索边界清晰 | 当前 BM25 为进程内简化实现 | 强全文、过滤、统一 Hybrid 需求可评 ES |
| Redis Streams vs Celery | 少组件，直接控制 PEL/claim | 自己补调度、退避和运维面 | 复杂任务调度和成熟生态需求上升时用 Celery |
| Redis Streams vs Kafka | 当前规模简单、延迟低 | 长期日志、分区回放能力弱 | 大吞吐事件平台和长期 replay 时评 Kafka |
| Redis Streams vs RabbitMQ | 复用 Redis，Consumer Group 足够 | 路由/成熟 broker 运维能力较弱 | 复杂路由、优先级、broker 语义需求提高时评 RabbitMQ |
| Postgres fact vs Redis state | durable truth 与 transient coordination 分离 | 双写不原子 | 保留分工，用 Outbox/CDC 修一致性而非混淆事实源 |
| At-least-once vs exactly-once claim | 不丢任务、实现现实 | 重复副作用 | 不承诺跨系统 exactly-once；用幂等实现 effectively-once |
| Offline eval vs online feedback | 可重复、低风险、便于回归 | 分布偏差，不能证明线上价值 | 上线后加 shadow/A-B、人工反馈与安全监控 |

## 面试统一句式

> 我不是先选技术再找理由，而是先明确语义和约束：事实是否需要事务、任务能否重复、查询是否词法与语义互补、延迟和成本预算是多少。当前方案满足参考实现目标，但在规模、真实流量或可靠性目标变化时，替代方案可能更合适。
