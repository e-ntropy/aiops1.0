# Hybrid RAG：执行流与状态

## 入库

1. `scripts/ingest_kb_corpus.py::collect_files()`读取版本化语料。
2. `split_markdown()`先按Markdown标题拆章节，再切parent，最后切child。
3. 每个child的page_content带章节前缀；metadata带source、chapter、parent_id、parent_content、chunk_index。
4. `get_vector_store().add_documents()`生成Embedding并写Milvus；`--reset`会删除Collection，必须显式授权。

## 查询

1. `build_context()`请求最终K的3倍child。
2. `advanced_search()`先Dense retrieve_k。
3. Hybrid开启时惰性构建BM25并做加权RRF。
4. Reranker开启且候选数大于final_k时精排。
5. 返回child后按parent_id保留第一次命中，最多final_k个parent。
6. context放parent正文，hits preview仍用child，兼顾解释命中与生成上下文。

## 评测

`score_hits()`把Gold表示为相关组：组内OR、组间AND式覆盖。Hit@K看是否命中任一组；MRR看第一个相关结果名次倒数；Recall看覆盖组数比例；最后对样本求均值。

