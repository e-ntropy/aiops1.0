# 第四条简历：异步运行时与评测体系面试题 30 题

> 回答口径：把“任务可靠运行”和“诊断结果正确”分开；把当前实现、历史实验和生产化建议分开。

## Q01：为什么 Postgres 与 Redis 要分工？

### 难度
基础

### 面试问题
为什么不只使用其中一个？

### 面试官考察点
事实存储与协调状态的边界。

### 推荐回答
Postgres 是任务、事件、证据、运行、工具调用、审批和报告的事实权威，适合事务、约束与审计；Redis Streams 负责排队、Consumer Group、PEL、超时认领和并发协调。只用 Redis 难以承担完整耐久事实关系，只用 Postgres 也能做队列但需要自行实现高效领取与 pending 语义。分工换来清晰职责，也引入双写一致性成本。

### 代码证据
`app/db/postgres.py` schema；`app/queue/redis_streams.py`。

### 面试官可能继续追问
1. Redis 是事实源吗？  
2. 两者不一致听谁的？

### 追问简答
不是，Redis 是瞬态协调面；业务状态以 Postgres 为准，再据此修复或重建队列状态。

### 我需要掌握的知识
System of Record、事务、协调状态。

## Q02：为什么诊断请求要异步排队？

### 难度
基础

### 面试问题
直接在 FastAPI 请求内运行有什么问题？

### 面试官考察点
长任务与高并发架构。

### 推荐回答
诊断会调用 LLM、MCP、检索与多 Agent，耗时和失败率都不适合绑定 HTTP 生命周期。持久化任务后排队，可削峰、限制昂贵资源并发、独立扩 Worker，并通过 SSE/状态接口观察进度。代价是最终一致、重复交付与恢复逻辑更复杂。

### 代码证据
`app/api/` 创建任务；`app/queue/`；`app/diagnosis_worker.py`。

### 面试官可能继续追问
1. Fast 模式也必须异步吗？  
2. 用户怎样拿结果？

### 追问简答
高并发主路径应异步，低风险演示可有同步兼容入口；客户端轮询任务或消费 SSE 事件。

### 我需要掌握的知识
异步任务、削峰、SSE。

## Q03：Redis Streams Consumer Group 解决什么？

### 难度
基础

### 面试问题
多个 Worker 如何避免每个都处理同一条新消息？

### 面试官考察点
Consumer Group 语义。

### 推荐回答
同一 Group 内，`XREADGROUP` 将新消息分配给某个 consumer，并把未确认消息记录在 PEL。不同 Worker 用不同 consumer 名共享负载；处理成功后 `XACK`。它不会自动提供 exactly-once，崩溃恢复仍可能重复处理。

### 代码证据
`ensure_group()` 创建 group；`read_new()` 使用 `XREADGROUP`。

### 面试官可能继续追问
1. 不同 Group 会怎样？  
2. consumer 离线后消息去哪？

### 追问简答
不同 Group 各自收到一份逻辑消费；离线 consumer 的未 ACK 消息留在 PEL，需 claim 给其他 consumer。

### 我需要掌握的知识
Stream、Group、consumer、PEL。

## Q04：解释 XADD、XREADGROUP、XACK 的完整链路。

### 难度
基础

### 面试问题
三个命令分别在什么时机调用？

### 面试官考察点
消息生命周期。

### 推荐回答
生产者用 `XADD` 把含 task_id 等字段的消息写入 stream；Worker 用 `XREADGROUP ... >` 读取尚未分配的新消息；完成 durable 状态更新后用 `XACK` 从 Group 的 PEL 移除。ACK 不是删除 stream 实体，只表示该 Group 已确认。

### 代码证据
`redis_streams.py::enqueue()`、`read_new()`、`ack()`。

### 面试官可能继续追问
1. 先 ACK 再写 DB 可以吗？  
2. stream 如何裁剪？

### 追问简答
不应，崩溃会造成已确认但事实未完成；裁剪要结合保留期、审计和未消费风险，不能盲目删除。

### 我需要掌握的知识
ack 时序、durability、stream retention。

## Q05：PEL 是什么？

### 难度
基础

### 面试问题
为什么它是恢复机制的核心？

### 面试官考察点
Pending Entries List。

### 推荐回答
PEL 记录已交付给 Group consumer 但未 ACK 的消息，并包含归属、空闲时间和投递信息。Worker 崩溃时消息不会重新作为“新消息”出现，必须检查或 claim PEL 才能恢复，因此它既是可靠性资产也是悬挂风险来源。

### 代码证据
`redis_streams.py::claim_stale()`；Worker 启动先处理 stale。

### 面试官可能继续追问
1. PEL 会无限增长吗？  
2. 如何监控？

### 追问简答
未 ACK 且未处理会增长；监控 pending 数、最大 idle、delivery count、最老消息年龄和 claim/DLQ 速率。

### 我需要掌握的知识
PEL、lag、idle time、可观测性。

## Q06：Pending recovery 是怎样完成的？

### 难度
中等

### 面试问题
Worker 重启后先做什么？

### 面试官考察点
恢复顺序。

### 推荐回答
Worker 循环会先调用 stale claim，获取超过最小 idle 时间的 pending 消息并尝试处理，然后再读新消息。处理时先查 Postgres task 状态：若已成功则只 ACK，否则继续执行或失败处理。这使“DB 已成功、ACK 前崩溃”可以安全收敛。

### 代码证据
`app/diagnosis_worker.py::run()`、`process_message()`。

### 面试官可能继续追问
1. idle 阈值太短有什么问题？  
2. 太长呢？

### 追问简答
太短会抢走仍在执行的长任务造成并发重复；太长会延迟真正崩溃任务的恢复。

### 我需要掌握的知识
lease、visibility timeout、恢复阈值。

## Q07：XAUTOCLAIM 的作用是什么？

### 难度
中等

### 面试问题
为什么不用重新读取新消息？

### 面试官考察点
Redis 恢复命令。

### 推荐回答
`XAUTOCLAIM` 扫描 Group PEL，把 idle 超阈值的消息所有权转给当前 consumer，并返回消息继续处理。新消息读取 `>` 不会返回已经交付的 pending，因此必须 claim。它简化了游标扫描，但仍需控制批量、idle 阈值和毒消息投递次数。

### 代码证据
`redis_streams.py::claim_stale()`。

### 面试官可能继续追问
1. claim 后原 consumer 恢复会怎样？  
2. 如何防并发执行？

### 追问简答
原任务代码可能仍在运行，所以会出现并发重复；需足够 lease、心跳续租或任务级 fencing/idempotency。

### 我需要掌握的知识
claim、lease、fencing token。

## Q08：为什么这是 at-least-once？

### 难度
基础

### 面试问题
请用一个崩溃窗口证明。

### 面试官考察点
交付语义。

### 推荐回答
业务处理和 DB 提交完成后、`XACK` 前若 Worker 崩溃，消息仍在 PEL，会被再次认领；因此同一任务可能至少执行一次甚至多次。好处是倾向不丢任务，代价是所有可重复路径都要设计幂等。

### 代码证据
Worker success 路径先持久化成功，再 ACK。

### 面试官可能继续追问
1. exactly-once 能否由 Redis 保证？  
2. at-most-once 怎么实现？

### 追问简答
跨 DB、工具和消息系统无法仅靠 Redis 保证；先 ACK 可近似 at-most-once，但崩溃会丢任务，不适合这里。

### 我需要掌握的知识
delivery semantics、崩溃一致性。

## Q09：重复执行会发生在哪些位置？

### 难度
中等

### 面试问题
不仅是消息重复，请列完整风险。

### 面试官考察点
端到端副作用意识。

### 推荐回答
可能来自 ACK 前崩溃、lease 过短导致并发 claim、producer 重投、retry 新消息与旧消息交叉、网络超时造成结果未知。重复的对象包括 LLM 成本、MCP 查询、外部通知、重启操作和数据库审计。只判断 task succeeded 能减少整任务重跑，但不能补偿“副作用已发生、task 未提交”的窗口。

### 代码证据
`diagnosis_worker.py` 的 succeeded 短路与 retry 重投；工具审计在 `app/runtime/`。

### 面试官可能继续追问
1. 读操作重复有害吗？  
2. 写工具如何保护？

### 追问简答
读操作也有成本和限流影响；写工具需审批、稳定 idempotency key、effect ledger 和不确定结果查询。

### 我需要掌握的知识
副作用、未知结果、重复成本。

## Q10：项目当前的幂等机制够吗？

### 难度
中等

### 面试问题
`task.status == succeeded` 能保证什么，不能保证什么？

### 面试官考察点
幂等层次。

### 推荐回答
它能处理“任务事实已成功但消息未 ACK”的重复交付：Worker 直接 ACK，不再跑图。但执行中崩溃、外部写操作已成功而 DB 未记录时仍可能重复，因此只是任务级短路，不是端到端 exactly-once。生产化要给每次副作用稳定操作键，并持久化开始、结果和未知状态。

### 代码证据
`diagnosis_worker.py::process_message()` L101 附近。

### 面试官可能继续追问
1. 幂等键如何生成？  
2. DB 唯一约束有何作用？

### 追问简答
可由 task_id + step/tool + logical attempt 生成；唯一约束阻止重复记录，但还需正确处理冲突后的已有结果。

### 我需要掌握的知识
idempotency key、唯一约束、effect ledger。

## Q11：当前重试与退避策略是什么？

### 难度
中等

### 面试问题
代码是否实现 exponential backoff？

### 面试官考察点
不把设计愿景说成现状。

### 推荐回答
Worker 捕获异常后，未耗尽 attempts 就把 task 改回 pending，`XADD` 一条新消息并 ACK 旧消息；当前没有看到基于时间的指数退避和 jitter，基本是立即重投。面试中应如实说明这是参考实现缺口，生产化需要 delayed scheduling、backoff、总 retry budget 和拥塞保护。

### 代码证据
`app/diagnosis_worker.py::_handle_failure()`。

### 面试官可能继续追问
1. 立即重试为什么危险？  
2. jitter 有何作用？

### 追问简答
依赖持续故障时会形成重试风暴；jitter 避免大量 Worker 同时再次冲击依赖。

### 我需要掌握的知识
backoff、jitter、retry storm。

## Q12：怎样区分可重试与不可重试错误？

### 难度
中等

### 面试问题
当前项目做到哪一步？

### 面试官考察点
错误分类与源码差距。

### 推荐回答
超时、连接中断、限流通常可重试；校验失败、权限拒绝、未知 Skill、非法参数通常不可重试；副作用超时属于结果未知，要先查询而不是盲重试。`AgentHarness` 有 `classify_error()` 概念，但 Worker 的 `_handle_failure()` 没接入分类，普通异常走统一 attempts 逻辑，因此完整策略尚未闭环。

### 代码证据
`app/runtime/agent_harness.py::classify_error()`；`diagnosis_worker.py::_handle_failure()`。

### 面试官可能继续追问
1. HTTP 429 怎么处理？  
2. 400 是否都不可重试？

### 追问简答
429 按 Retry-After 和配额退避；400 多为永久错误，但需按 provider 错误码分类，不能只看状态码。

### 我需要掌握的知识
错误分类、retryability、unknown outcome。

## Q13：DLQ 的作用与当前边界是什么？

### 难度
中等

### 面试问题
什么时候进入 DLQ？

### 面试官考察点
失败隔离。

### 推荐回答
attempts 耗尽后，Worker 将 task 标记 failed，把消息与错误信息写入 DLQ，再 ACK 原消息，避免毒消息永久堵塞主 stream。当前代码能写 DLQ，但没有找到完整的 list/inspect/replay/skip 运维管理面，所以它实现了隔离，尚未形成处置闭环。

### 代码证据
`redis_streams.py` 的 DLQ 写入；`diagnosis_worker.py::_handle_failure()`。

### 面试官可能继续追问
1. DLQ 是否可以无限保留？  
2. 需要哪些告警？

### 追问简答
需设保留和归档策略且保护敏感信息；监控进入速率、积压量、错误类别和最老年龄。

### 我需要掌握的知识
DLQ、poison message、告警。

## Q14：如何安全 replay DLQ？

### 难度
高级

### 面试问题
能否把 DLQ 全部重新 XADD？

### 面试官考察点
恢复操作的风险控制。

### 推荐回答
不能无脑全量重放。应先按错误原因确认依赖已恢复，检查 task 当前事实状态和副作用记录；生成新的 replay 操作 ID，保留原 message/attempt 关联，限速分批重投并再次执行权限审批。当前仓库未实现完整 replay API，这是明确改进项。

### 代码证据
只找到 DLQ 写入路径，未找到完整 replay 管理链。

### 面试官可能继续追问
1. replay 是否重置 attempts？  
2. 谁能操作？

### 追问简答
应按错误修复策略显式决定并设新预算，不能隐式清零；需要受控运维权限、审计和高风险审批。

### 我需要掌握的知识
replay、审计、限速、权限。

## Q15：分布式并发如何限制？

### 难度
中等

### 面试问题
多 Worker 下本地 Semaphore 为什么不够？

### 面试官考察点
全局资源控制。

### 推荐回答
本地 Semaphore 只能限制单进程，多个 API/Worker 会各自放大并发。项目使用 Redis 分布式 limiter 协调全局槽位；手工请求可快速失败，Worker 可等待槽位，以保护模型供应商和基础设施。仍需处理租约过期、Worker 崩溃释放、公平性和按 provider 分桶。

### 代码证据
`app/core/distributed_limiter.py`；`app/services/aiops_service.py`。

### 面试官可能继续追问
1. Redis 挂了怎么办？  
2. 如何避免槽位泄漏？

### 追问简答
按风险选择 fail-closed 或受限本地降级；使用带 TTL 的 lease、心跳与最终清理。

### 我需要掌握的知识
distributed semaphore、lease、限流。

## Q16：Worker 处理过程中崩溃会怎样？

### 难度
中等

### 面试问题
分别讨论 DB 提交前后。

### 面试官考察点
崩溃点推演。

### 推荐回答
DB 成功提交前崩溃，消息留在 PEL，后续 claim 后可能重新执行；DB 已标 succeeded、ACK 前崩溃，后续 claim 会命中 succeeded 短路并 ACK。第一种仍可能重复此前已发生但未记录的外部副作用，因此需 checkpoint 与工具级幂等。

### 代码证据
Worker success 时序、`process_message()` 状态检查。

### 面试官可能继续追问
1. LangGraph checkpoint 能解决全部问题吗？  
2. 如何处理长任务？

### 追问简答
不能自动保证外部副作用原子性；长任务需心跳/续租、阶段 checkpoint 和取消语义。

### 我需要掌握的知识
crash recovery、checkpoint、副作用原子性。

## Q17：Postgres 与 Redis 不一致有哪些场景？

### 难度
高级

### 面试问题
请列出双写窗口及结果。

### 面试官考察点
分布式事务思维。

### 推荐回答
DB 创建 task 后、XADD 前崩溃会产生无消息任务；XADD 后、DB 保存 message_id 前崩溃会产生引用不完整；retry 时新 XADD 成功但旧消息未 ACK 会出现两条可处理消息。当前可依赖 task_id 和状态做部分收敛，但没有原子保证。更强方案是 Postgres Transactional Outbox，由 dispatcher 投递并幂等标记。

### 代码证据
任务持久化与 `redis_streams.enqueue()` 分属不同系统；无 outbox 表/dispatcher 主链。

### 面试官可能继续追问
1. Outbox 是否保证 exactly-once？  
2. 如何清理 outbox？

### 追问简答
通常仍是 at-least-once 发布，需要消费者幂等；按投递状态和保留期归档清理，并保留审计。

### 我需要掌握的知识
dual write、Transactional Outbox、CDC。

## Q18：ACK 前后崩溃的差异是什么？

### 难度
中等

### 面试问题
为什么 ACK 时序决定可靠性？

### 面试官考察点
确认边界。

### 推荐回答
业务成功后 ACK 前崩溃会重复交付但不丢；先 ACK 后业务完成前崩溃则消息不再 pending，可能永久丢任务。项目选择先持久化成功再 ACK，符合 at-least-once，但必须配套幂等。ACK 返回超时也要按“不确定结果”处理，不能简单假定失败。

### 代码证据
`diagnosis_worker.py` 的 success path。

### 面试官可能继续追问
1. ACK 超时后怎么办？  
2. Redis 重启会怎样？

### 追问简答
查 PEL/任务事实后收敛；Redis 持久化与集群配置决定消息耐久性，不能只由应用代码保证。

### 我需要掌握的知识
ack boundary、ambiguous result、Redis durability。

## Q19：Postgres 不可用时怎么办？

### 难度
中等

### 面试问题
Worker 能否继续 ACK？

### 面试官考察点
事实权威与 fail-closed。

### 推荐回答
不能在无法确认 durable task 状态时 ACK，否则可能丢失事实。应暂停或有限重试，让消息保留 pending，并通过 DB 熔断、告警和全局背压避免 Worker 风暴。若执行已经产生外部副作用，则记录不确定结果并在 DB 恢复后对账。

### 代码证据
Postgres 是任务事实源；Worker ACK 位于持久化之后。

### 面试官可能继续追问
1. pending 会不会爆？  
2. API 是否继续接单？

### 追问简答
会，所以需健康门禁与背压；ready check 应失败或限制新任务进入，不能无限积压。

### 我需要掌握的知识
backpressure、readiness、事实一致性。

## Q20：Redis 不可用时怎么办？

### 难度
中等

### 面试问题
任务已写入 Postgres 但无法 enqueue 如何恢复？

### 面试官考察点
消息基础设施故障。

### 推荐回答
当前双写模型会留下 pending task 无消息，应用可返回失败或待恢复状态，但若没有扫描 dispatcher，自动恢复不完整。生产化用 Outbox 把“需要发布”与业务事务一起提交，Redis 恢复后重投；Worker 侧则停止读取，不能假装成功。

### 代码证据
当前无完整 Outbox 主链；Redis 是 queue/limiter 依赖。

### 面试官可能继续追问
1. 是否退化为同步执行？  
2. limiter 也在 Redis 怎么办？

### 追问简答
通常不应在故障时把高并发任务突然转同步；高成本操作应 fail-closed 或采用严格受限的本地额度。

### 我需要掌握的知识
outage mode、Outbox、降级策略。

## Q21：如何处理永久 pending 或毒消息？

### 难度
高级

### 面试问题
仅靠 XAUTOCLAIM 有什么问题？

### 面试官考察点
恢复闭环。

### 推荐回答
XAUTOCLAIM 只转移所有权，不判断消息是否永远失败；毒消息可能反复 claim。应结合 delivery/attempt、消息年龄和错误分类，超过预算进入 DLQ，并告警；同时要防长任务被误判 stale，可用 heartbeat/lease renewal。当前 attempts 主要在 payload/Worker 逻辑中，完善 PEL 观测仍是改进项。

### 代码证据
`claim_stale()` 与 `_handle_failure()`。

### 面试官可能继续追问
1. message attempt 与 task attempt 如何统一？  
2. 如何避免人工遗忘 DLQ？

### 追问简答
以 task run/attempt 表为事实，消息只携带引用；建立 SLO 告警和处置工单闭环。

### 我需要掌握的知识
poison message、retry budget、SLO。

## Q22：为什么不用 Celery、Kafka 或 RabbitMQ？

### 难度
高级

### 面试问题
当前选 Redis Streams 的合理边界是什么？

### 面试官考察点
选型而非站队。

### 推荐回答
Redis 已用于协调与限流，Streams 能用较少组件提供 Group、PEL 与 claim，适合个人参考实现。Celery 提供成熟任务抽象、重试与调度；RabbitMQ 强于传统 broker 路由与确认；Kafka 强于高吞吐持久日志、分区回放和生态。若需求转向复杂调度、长期事件留存或大吞吐，应按运维能力与语义重新评估，仓库没有基准证明 Streams 普遍更优。

### 代码证据
当前 Compose 与 `app/queue/redis_streams.py` 实现 Redis Streams；其他方案未实现。

### 面试官可能继续追问
1. Celery 能否解决双写？  
2. Kafka 就是 exactly-once 吗？

### 追问简答
不能自动解决业务 DB 与 broker 双写；Kafka 的事务语义也不自动覆盖外部 DB/工具副作用。

### 我需要掌握的知识
消息系统选型、任务队列、事件日志。

## Q23：为什么要分四层评测？

### 难度
基础

### 面试问题
只做 E2E 不行吗？

### 面试官考察点
可诊断评测体系。

### 推荐回答
E2E 失败无法区分是 Skill 路由、证据召回、生成忠实性还是诊断图编排问题。项目分别评 Router、Retrieval、RAGAS 和 diagnosis E2E，使问题可定位、迭代成本更低。分层指标不能互相替代：某层高分也不保证最终 RCA 正确。

### 代码证据
`benchmark/` 下四类 runner、dataset 和 reports。

### 面试官可能继续追问
1. 层间如何关联？  
2. 最终发布门禁看什么？

### 追问简答
使用统一 case/source/run_id 追踪错误传播；门禁应组合可靠性、效果、延迟、成本与安全指标。

### 我需要掌握的知识
evaluation pyramid、错误归因、发布门禁。

## Q24：Router 评测如何计算？

### 难度
中等

### 面试问题
40 条数据的构成和结果是什么？

### 面试官考察点
准确复述历史结果。

### 推荐回答
数据集共 40 条，其中 35 条期望某个 Skill，5 条是 OUT_OF_SCOPE。历史报告总体 30/40，即 75%；OOS 是 5/5，Skill 命中是 25/35。必须同时报告分项，否则总体准确率会掩盖具体 Skill 混淆。

### 代码证据
`benchmark/skill_router_eval.jsonl`；`run_skill_router_benchmark.py` 与历史报告。

### 面试官可能继续追问
1. OOS 为什么重要？  
2. 还需要什么指标？

### 追问简答
它关系到拒识和安全边界；可补每类 precision/recall、混淆矩阵、top-k 与置信度校准。

### 我需要掌握的知识
分类评测、OOS、混淆矩阵。

## Q25：Retrieval 评测如何执行？

### 难度
中等

### 面试问题
50 条查询怎样得到 Hit、MRR、Recall？

### 面试官考察点
数据与聚合公式。

### 推荐回答
runner 对每条 query 调检索，检查 top-k 文本是否覆盖 gold 关键词组。Hit 是是否至少命中一组，MRR 是首个命中位置倒数，Recall 是命中 gold 组数占总组数，再对 50 条平均。历史 Hybrid 报告 top-3 为 Hit .86、MRR .7767、Recall .86；这是关键词代理指标，不是生成正确率。

### 代码证据
`benchmark/run_benchmark.py::score_hits()`；`retrieval_rk_50.jsonl`；历史 retrieval JSON。

### 面试官可能继续追问
1. Gold 组为什么用 OR？  
2. Recall 为何可能和 Hit 不同？

### 追问简答
OR 容纳同义表达；一个 query 有多组证据时可能只命中部分，Hit 为 1 而 Recall 小于 1。

### 我需要掌握的知识
IR metrics、关键词 gold、macro average。

## Q26：RAGAS 评测说明了什么？

### 难度
中等

### 面试问题
历史 RAGAS 报告用了什么 judge，结果能否当真值？

### 面试官考察点
LLM-as-judge 边界。

### 推荐回答
保存报告包含 50 条 QA，judge 是 `deepseek-chat`，本地 embedding 为 `BAAI/bge-small-zh-v1.5`，报告均值包括 faithfulness .8689、answer relevancy .8849、context precision .8833、context recall .8817 等，errors 为空。它说明该次环境运行的代理质量，不是真值；judge 偏差、提示、模型版本和样本选择都影响结果，需要人工抽检和重复实验。

### 代码证据
`benchmark/reports/ragas_20260728-165320.json`；`ragas_qa_50.jsonl`。

### 面试官可能继续追问
1. faithfulness 与 correctness 区别？  
2. judge 自己犯错怎么办？

### 追问简答
忠实性只看回答是否受上下文支持，不保证上下文本身正确；用多 judge、一致性检查和人工校准集估计偏差。

### 我需要掌握的知识
RAGAS、LLM-as-judge、人工校准。

## Q27：E2E 的“成功”具体指什么？

### 难度
中等

### 面试问题
10/10 跑通是否等于 10/10 诊断正确？

### 面试官考察点
可靠性指标与任务效果分离。

### 推荐回答
不等于。保存的 Fast、Deep 各 10 次历史运行都成功完成，说明当时环境下流程可执行；但 RCA top-1 命中分别约 .5 和 .0，说明完成与正确是两件事。Deep 的低结果还受到合成事故、gold 定义和本地证据污染等限制，不能由此断言架构必然更差。

### 代码证据
`benchmark/run_diagnosis_benchmark.py` 及保存的 E2E 报告/总结。

### 面试官可能继续追问
1. E2E 应报告哪些维度？  
2. 为什么 Deep 可能更差？

### 追问简答
成功率、RCA 排名、证据覆盖、延迟、成本、错误类别；复杂图会放大噪声，数据和 judge 也可能不适配。

### 我需要掌握的知识
task success、semantic correctness、benchmark validity。

## Q28：160 条评测是怎样组成的？

### 难度
基础

### 面试问题
“构建 160 条评测集”这句话准确吗？

### 面试官考察点
诚实拆数。

### 推荐回答
准确拆分是 40 条 Router、50 条 Retrieval、50 条 RAGAS QA，以及 Fast 10 + Deep 10 共 20 个 E2E 运行单元，合计 160。它们任务定义和粒度不同，不能说成 160 条完整端到端用例。简历可以写“构建四层共 160 个评测 case/运行单元”，面试主动拆开。

### 代码证据
三个 JSONL 的行数与 diagnosis benchmark 的 mode/repeat 配置。

### 面试官可能继续追问
1. 为什么 E2E 叫运行单元而非独立样本？  
2. 能否合并算总准确率？

### 追问简答
它是 10 个事故在两模式下的运行/或按报告定义的重复单位，需看具体报告；不同任务分母与指标不同，不能合并成一个准确率。

### 我需要掌握的知识
样本单位、运行次数、指标口径。

## Q29：50 条 QA 如何构造与人工校验？

### 难度
高级

### 面试问题
怎样让小型 RAG 评测集更可信？

### 面试官考察点
数据集工程。

### 推荐回答
应从 corpus 章节和真实故障类型分层抽样，记录 question、reference、source、所需证据组和难度；至少由第二人核对答案可支持性、歧义和跨文档依赖。再做模板/语义去重，冻结 test，新增真实用户 query 作为外部集。仓库有 50 条版本化 JSONL，但提交历史本身不能证明完整双人标注流程，所以面试不要虚构审核人数。

### 代码证据
`benchmark/ragas_qa_50.jsonl`、`retrieval_rk_50.jsonl`；未见完整标注审计记录。

### 面试官可能继续追问
1. 如何测标注一致性？  
2. 合成问题能否使用？

### 追问简答
双人独立标注并计算一致率/Kappa，再仲裁；可用于扩充，但需与真实查询分层报告并抽检。

### 我需要掌握的知识
dataset curation、inter-annotator agreement、外部有效性。

## Q30：如何检查数据重叠并判断指标可信度？

### 难度
高级

### 面试问题
你会怎样审计这套 160 口径？

### 面试官考察点
泄漏、复现和统计完整性。

### 推荐回答
先锁定数据、corpus、代码、模型与配置版本；检查 query/answer 在 train/dev/test、知识库和不同评测层之间的精确及语义重复；逐条保存输出和失败，按场景分桶，做 bootstrap 置信区间和重复运行。指标还要同时报告延迟、成本、缓存命中和 provider 失败。当前历史报告能作为可审计证据，但样本小、单次环境、无完整置信区间，所以可信度应表述为“阶段性离线基线”。

### 代码证据
版本化 datasets/reports 提供复核入口；报告尚未覆盖上述全部统计与数据血缘。

### 面试官可能继续追问
1. 缓存会怎样污染结果？  
2. 模型随机性如何控制？

### 追问简答
缓存可能降低延迟或复用旧输出，应分别冷/热跑并记录 key；固定版本与温度、重复多次，并报告均值、方差和原始结果。

### 我需要掌握的知识
数据泄漏、可复现性、置信区间、实验血缘。
