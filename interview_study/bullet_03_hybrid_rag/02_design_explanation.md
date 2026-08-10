# Hybrid RAG：设计解释

## 为什么运维知识难检索

告警名、错误码、PromQL、命令等精确token需要词法匹配；自然语言故障现象又需要语义匹配。长SOP直接Embedding会稀释局部信号，切太小又会丢完整处置上下文。

## Parent-Child

Child小块参与Embedding/BM25，提高局部命中；Parent保留章节/流程完整性，生成时回填。项目将parent_content冗余进每个child metadata，换取查询简单，代价是Milvus存储膨胀与parent更新一致性风险。

## Hybrid与加权RRF

Dense捕获语义，BM25捕获精确token。两路分数尺度不可比，因此按排名融合：`score(d)=Σ weight_i/(k+rank_i)`。权重允许控制BM25贡献，`rrf_k`控制头部排名差异。相比直接归一化加权，它对分数分布更稳；代价是丢弃绝对置信度。

## 降级

Milvus/向量失败返回空；BM25不可用回纯向量；Reranker失败回融合候选；旧索引无parent_id时按content hash去重并用child本身。降级保证可用，不保证相同质量。

## 适用与替代

Milvus适合独立向量服务与扩展；小数据可FAISS；已有Postgres可pgvector；需要强词法、过滤和运维成熟度可Elasticsearch/OpenSearch。当前选择适合展示向量基础设施，但增加etcd/MinIO/Milvus运维成本。

