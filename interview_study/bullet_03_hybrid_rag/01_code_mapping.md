# 第三条简历：Hybrid RAG——代码映射

## 简历原句

> 针对运维知识召回不足，实现 Parent-Child 分块、Milvus 子块召回及父文档回溯，结合 BM25、向量检索与 RRF 融合，使 Hit@3 从 0.800 提升至 0.860，MRR@3 从 0.710 提升至 0.777。

## 主调用链

```text
Markdown corpus
-> splitter.split_markdown
-> child Document(parent_id,parent_content,chapter,source)
-> Embedding
-> Milvus
-> vector_store.advanced_search
   -> Vector top-N
   -> BM25 top-N
   -> weighted RRF
   -> optional Reranker
-> rag.retrieval.build_context
-> parent_id去重
-> parent_content回填上下文
-> RAG Chat / knowledge Tool / benchmark
```

| 原子能力 | 文件/符号 | 关键字段/配置 | 结论 |
| --- | --- | --- | --- |
| 标题感知切分 | `app/core/splitter.py::split_markdown()` L116 | chapter/source | 已实现 |
| Parent块 | 同函数 L152-L166 | `rag_parent_max_chars` | 已实现 |
| Child块 | L167-L200 | chunk size/overlap | 已实现 |
| 关联 | L183-L196 | `parent_id`,`parent_content` | 元数据冗余关联 |
| Milvus向量库 | `app/core/vector_store.py::get_vector_store()` L31 | HNSW/COSINE/content/vector | 已实现 |
| Embedding | `app/core/embedding.py::get_embeddings()` | provider/model/dim | 已实现 |
| Dense粗排 | `safe_similarity_search()` L85；`advanced_search()` L116 | retrieve_k | 主链 |
| BM25 | `hybrid_retriever.py::_BM25Index` L88 | 进程内、Milvus全量加载 | 已实现 |
| RRF | `hybrid_search()` L241 | weight/(rrf_k+rank+1) | 加权RRF |
| Rerank | `vector_store.py` L179 | 可选 | 实现存在；历史核心提升不依赖它 |
| Parent回溯 | `rag/retrieval.py::build_context()` L25 | 按parent_id去重、读parent_content | 已实现；不是二次DB查询 |
| Hit/MRR/Recall | `benchmark/run_benchmark.py::score_hits()` L120 | 分组Gold | 已实现 |
| 0.800/0.710 | retrieval报告132308 | hybrid=false, rerank=true | 纯向量基线 |
| 0.860/0.777 | retrieval报告132213 | hybrid=true, weight=.4 | Hybrid结果 |

## 表述边界

- A/B直接隔离的是Hybrid开关；两边都使用Parent-Child，因此不能把全部提升归因于Parent-Child。
- “父文档回溯”是从命中child的metadata读取冗余parent_content，不是独立父表查询。
- BM25索引在进程内，从Milvus最多拉16384 chunks；适合参考实现，不是大规模生产方案。
- 50条上Hit多3条；未做显著性检验或置信区间。

