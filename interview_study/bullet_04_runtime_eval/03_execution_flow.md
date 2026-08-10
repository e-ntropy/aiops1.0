# 第四条简历：执行流程

## 正常路径

```text
create task in Postgres
-> XADD diagnosis stream
-> XREADGROUP by worker consumer
-> acquire distributed slot
-> load task / check succeeded
-> run fast or deep graph
-> persist report/audit/task=succeeded
-> XACK
```

## 失败与恢复路径

```text
worker crash before ACK
-> message remains in PEL
-> another worker XAUTOCLAIM
-> check durable task state
-> resume/re-execute or ACK completed task

exception
-> attempts remaining: task=pending -> XADD retry -> save new message id -> XACK old
-> attempts exhausted: task=failed -> XADD DLQ -> XACK old
```

## 一致性风险窗口

| 窗口 | 可能结果 | 当前缓解 | 更强方案 |
| --- | --- | --- | --- |
| DB 成功、XADD 前崩溃 | 有任务无消息 | 状态扫描可发现，但未形成完整 dispatcher | Transactional Outbox |
| XADD 成功、保存 message id 前崩溃 | 消息存在，DB 引用不完整 | Worker 仍可按 payload task_id 查事实 | 幂等 producer/outbox |
| 业务完成、DB 提交前崩溃 | 重复执行 | at-least-once + 状态检查 | checkpoint + 副作用幂等 |
| DB 成功、ACK 前崩溃 | 重复投递 | succeeded 短路并 ACK | 保持现策略并完善审计 |

