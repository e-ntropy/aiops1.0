# 一分钟项目介绍

我做的是一个面向 OnCall/SRE 场景的多 Agent AIOps 诊断工作台。它接收用户故障描述或 Alertmanager 事件，先通过 Skill Router 选择诊断手册，再按复杂度进入 Fast 或 Deep 两条 LangGraph 链路：Fast 强调低延迟闭环，Deep 会让多个专业 Agent 隔离取证，再归并为带来源的 Evidence，最后生成 RCA 和处置建议。

知识侧我实现了 Parent-Child RAG：Child 在 Milvus 做语义召回，BM25 补错误码和命令匹配，再用 weighted RRF 融合，生成时回填 Parent。仓库历史 50 条离线实验中，Hybrid 相对向量基线的 Hit@3 从 0.800 到 0.860，MRR@3 从 0.710 到约 0.777；这是一次小样本观察，主要证明 Hybrid 配置差异，不能夸成 Parent-Child 或 reranker 的独立因果收益。

工程侧用 Postgres 保存事实和审计记录，Redis Streams 负责异步队列、Consumer Group、PEL 恢复和 DLQ。整体有 Router、Retrieval、RAGAS、E2E 四层共 160 个评测 case/运行单元。这个项目最大的价值不是“模型自动解决一切”，而是把路由、证据、权限、运行状态和评测做成可追踪链路；当前仍有 Outbox、工具级幂等、真实数据与统计显著性等生产化缺口。

