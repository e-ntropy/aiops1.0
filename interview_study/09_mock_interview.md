# 综合模拟面试

## 使用方法

第一轮只看“问题”口述；第二轮对照“合格答案要点”；第三轮让同伴从追问中任选两项。每题建议 60–120 秒，系统设计题 3–5 分钟。

## 第一组：项目全貌

### 1. 请用一分钟介绍项目。

合格答案要点：AIOps 场景；Skill Router；Fast/Deep；Evidence；Hybrid RAG；Postgres/Redis；四层评测；主动给一个边界。

追问：为什么需要 Agent 而不是普通工作流？哪些步骤必须确定性？

### 2. 画出一次 Deep 诊断的调用链。

合格答案要点：incident context → evidence plan → specialist fan-out → reduction → RCA → remediation proposal → report；私有状态不共享，只共享 Evidence。

追问：某 specialist 超时怎么办？冲突证据怎样处理？

### 3. Fast 与 Deep 如何路由？

合格答案要点：复杂度、影响范围、跨域证据、延迟/成本预算；当前模式选择实现与理想自适应路由要区分。

追问：误路由的代价是什么？如何离线标注路由 gold？

## 第二组：Skill 与安全

### 4. Skill 是 prompt、代码还是配置？

合格答案要点：模型/loader/registry/playbook/documentation 的组合；定义允许工具、步骤和约束，不只是 prompt。

追问：热加载、一致性、版本回滚怎样做？

### 5. OUT_OF_SCOPE 为什么是能力而不是失败？

合格答案要点：拒绝不具备 playbook/权限/证据的问题；降低错误工具调用和幻觉；需测困难负例，不能因 5/5 就宣称 100%。

追问：低置信度时返回 OOS 还是人工确认？

### 6. 怎样保证 Agent 不会随意重启容器？

合格答案要点：tool filter、permission、guardrail、approval、audit；Docker restart 不是只读；`bypass` 仅开发。

追问：审批后请求参数被修改怎么办？

## 第三组：RAG

### 7. Parent-Child 的存储方式有什么优缺点？

合格答案要点：Child 在 Milvus，metadata 冗余 parent content；少一次查询；存储/更新放大；生产可拆 Parent store。

追问：如何无停机迁移？

### 8. 写出 weighted RRF 公式并解释权重。

合格答案要点：`Σ weight_i/(rrf_k+rank_i+1)`；规避异构分数校准；权重需验证集调优。

追问：BM25 和 dense 冲突怎么审计？

### 9. 0.800 到 0.860 是否显著？

合格答案要点：50 条上 40→43，只多 3 条；同 query 配对；需逐题 win/loss、McNemar/paired bootstrap、CI；只说历史观察 +6pp。

追问：这个 A/B 究竟隔离了哪个变量？

### 10. reranker 在报告中是否有效？

合格答案要点：保存的 Hybrid 有/无 rerank 两份指标相同；不能证明收益，也不能证明永远无效；需要更难候选集、排序指标和延迟成本实验。

追问：为什么 top-k 太小会限制 reranker？

## 第四组：异步运行时

### 11. 为什么系统是 at-least-once？

合格答案要点：业务/DB 成功后 ACK；ACK 前崩溃导致重复；PEL + XAUTOCLAIM；以不丢换重复。

追问：哪些外部副作用最危险？

### 12. DB 成功但 XADD 失败怎么办？

合格答案要点：当前可能形成无消息 task；状态扫描只是补救；生产用 Transactional Outbox + 幂等 dispatcher。

追问：Outbox 是否等于 exactly-once？

### 13. Worker 崩溃后如何恢复？

合格答案要点：消息留 PEL；idle 超阈值由 XAUTOCLAIM 转移；检查 durable task；succeeded 短路，否则重跑/恢复。

追问：长任务被误 claim 怎么办？

### 14. 当前 retry 有哪些缺口？

合格答案要点：统一异常 attempts；未接 `classify_error()`；无指数退避/jitter；结果未知副作用无专门恢复；DLQ replay 管理面不足。

追问：怎样定义 retry budget？

## 第五组：评测

### 15. 160 条如何拆？

合格答案要点：40 Router + 50 Retrieval + 50 RAGAS + Fast/Deep 10×2；是 case/运行单元，不是 160 E2E。

追问：为什么这些指标不能求一个总体平均？

### 16. Router 75% 怎么来的？

合格答案要点：30/40；Skill 25/35；OOS 5/5；需混淆矩阵和更大 OOS。

追问：类别不平衡时 accuracy 有什么问题？

### 17. RAGAS .869 能说明什么？

合格答案要点：50 条、特定 judge/model 的 faithfulness 历史均值；代理指标；不能替代人工事实正确性；需多 judge/抽检/CI。

追问：上下文本身错误但回答忠实时怎么办？

### 18. 为什么 E2E 10/10 成功但 RCA 很差？

合格答案要点：运行可靠性与语义正确性是不同维度；合成事故、gold、证据污染、复杂图噪声；需逐层定位。

追问：先修模型还是先修数据？为什么？

## 第六组：系统设计压力题

### 19. 如果流量扩大 100 倍，你先改哪里？

合格答案要点：先量测瓶颈；queue lag、provider rate、Milvus、DB、BM25 每进程全量加载；Outbox；分桶限流；索引服务化；容量与恢复演练。

追问：如何定义 SLO 和降级顺序？

### 20. 如果明天要用于生产，你最不放心什么？

合格答案要点：真实数据与效果证据不足、写工具副作用幂等、DB/Redis 双写、错误分类/退避、DLQ 运维、权限/秘密、E2E/CI 缺口；按风险和可验证性排序。

追问：两周内只能完成三项，你选什么？

## 评分标准

| 维度 | 0 分 | 1 分 | 2 分 |
| --- | --- | --- | --- |
| 源码证据 | 只背概念 | 能说模块 | 能指出调用链/字段/时序 |
| 边界诚实 | 夸成生产 | 偶尔限定 | 主动区分实现、历史实验、建议 |
| 指标 | 只背数字 | 会说分母 | 会说公式、样本、显著性和失败处理 |
| 取舍 | 只说优点 | 能说缺点 | 能说约束变化时如何换方案 |
| 故障推演 | 泛泛说重试 | 能说单点故障 | 能逐崩溃窗口推导重复/丢失/悬挂 |

总分 10：8–10 可进入系统设计深挖；5–7 继续补源码链；0–4 先停止背数字，回到四个 bullet 的 code mapping。

