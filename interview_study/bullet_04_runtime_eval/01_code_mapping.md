# 第四条简历：异步运行时与评测体系——代码映射

## 建议简历口径

> 设计并实现 Postgres 事实存储、Redis Streams 异步任务队列与 Worker 恢复链路，覆盖 Consumer Group、PEL、XAUTOCLAIM、重试和 DLQ；建立 Router、Retrieval、RAGAS 与诊断 E2E 四层离线评测。历史评测资产合计 160 个 case/运行单元，其中并非 160 条完整端到端样本。

## 运行时主链

```text
HTTP/Alert -> Postgres task fact -> Redis XADD
Worker XREADGROUP -> task status/idempotency check -> diagnosis
success: Postgres succeeded -> XACK
failure: Postgres pending/failed -> retry XADD or DLQ -> XACK old message
startup/recovery: XPENDING/PEL -> XAUTOCLAIM -> resume
```

| 能力 | 文件/符号 | 事实结论 |
| --- | --- | --- |
| 事实表与约束 | `app/db/postgres.py` L85-L269 | Alert、Incident、Task、Evidence、Run、Tool、Approval、Report 等事实 |
| Stream/Group | `app/queue/redis_streams.py::ensure_group()`、`enqueue()` | `XGROUP`、`XADD` |
| 消费与确认 | `read_new()`、`ack()` | `XREADGROUP`、`XACK` |
| Pending 恢复 | `claim_stale()` | `XAUTOCLAIM` |
| Worker | `app/diagnosis_worker.py::run()` | 启动先恢复 stale，再读新消息 |
| 幂等短路 | `process_message()` L101 | task 已 `succeeded` 时直接 ACK |
| 重试/DLQ | `_handle_failure()` L161 | 新消息重投；耗尽后 DLQ |
| 分布式并发 | `app/core/distributed_limiter.py` | Redis 协调并发槽位 |
| Router 评测 | `benchmark/run_skill_router_benchmark.py` | 40 条：35 Skill + 5 OOS |
| Retrieval | `benchmark/retrieval_rk_50.jsonl` | 50 条检索查询 |
| RAGAS | `benchmark/ragas_qa_50.jsonl` | 50 条 QA |
| E2E | `benchmark/run_diagnosis_benchmark.py` | Fast/Deep 各 10 次，共 20 运行单元 |

## 关键边界

- Postgres 与 Redis 之间没有跨系统原子事务或 Outbox；存在状态/消息不一致窗口。
- 当前 Worker 将一般异常统一重试，`AgentHarness.classify_error()` 没有接入该重试决策，也未实现指数退避。
- DLQ 能写入，但没有找到完整的管理与 replay API。
- `160=40+50+50+20`，是四层评测资产/运行单元之和，不能包装成 160 条 E2E。
- 10×2 E2E 是仓库保存的环境特定历史结果；成功运行不等于诊断 top-1 正确。

